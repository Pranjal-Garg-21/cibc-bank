"""Agent dashboard in the terminal: sign in once, see your cases, then just type questions.

    python scripts/agent_dashboard.py

You sign in with your agent ID (AG-2407). Your cases on the snapshot date are listed. Then type plain text:

    3                                         open case 3 from your list (or type its case ID)
    what did the customer agree to on the last call?      asked about the open case
    Summarise the most recent call on case CS-2026-614370  a case ID in the question opens that case
    What is the grace period before a promise is broken?   a rules question goes to the policy documents
    policy: ...   /  case: ...                force where the question goes, if the guess is wrong

Short words also work, with or without "/": cases, close, sources, help, quit.
Questions in French are answered in French.

Needs OPENROUTER_API_KEY (environment or the gitignored .env), the gold layer, and for rules questions
warehouse/rag/policy_chunks.jsonl (python -m trustissues.ask.rag.policy_ingest).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    import readline  # noqa: F401  (arrow keys and history; not available on Windows)
except ImportError:
    pass

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from trustissues.ask.rag.customer_path import (CustomerAccessDenied, DENIED, can_view_customer, resolve_case,
                                               run_customer)
from trustissues.ask.rag.identify import identify
from trustissues.common import config
from trustissues.common.audit import AuditLog
from trustissues.common.db import connect
from trustissues.common.llm import LLM, LLMError

console = Console()
AS_OF = config.AS_OF_DATE
ROLE_OF = {"agent": "agent", "senior_agent": "agent", "hardship_specialist": "agent", "team_lead": "team_lead"}
KIND_LABEL = {"note": "note", "transcript": "call", "contact": "contact", "promise": "promise", "payment": "payment",
              "offer": "offer"}


@dataclass
class Session:
    agent_id: str
    name: str
    role: str  # agent | team_lead
    team: str
    cases: list[dict] = field(default_factory=list)
    case_id: str | None = None
    customer_id: str | None = None
    sources: bool = False


# ------------------------------------------------------------------ data

def open_db():
    """Writable if we can (the audit log is written); read-only if another process holds the database."""
    try:
        return connect(read_only=False), True
    except Exception:
        return connect(read_only=True), False


def sign_in(con, agent_id: str) -> Session | None:
    row = con.execute("SELECT agent_id, agent_display_name, role, team_id FROM curated.agents WHERE agent_id = ?",
                      [agent_id.upper()]).fetchone()
    if not row or row[2] not in ROLE_OF:
        return None
    return Session(row[0], row[1] or row[0], ROLE_OF[row[2]], row[3] or "")


CASE_COLS = """k.case_id, k.primary_product, k.current_bucket, k.current_dpd, k.total_overdue, k.next_scheduled_action,
               k.current_ptp_amount, k.current_ptp_due_date, k.hardship_flag, k.dispute_flag, k.cease_contact_flag,
               k.vulnerable_customer_flag, k.case_status, k.queue, k.last_contact_ts, a.agent_id"""


def _case_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_cases(con, s: Session, limit: int = 25) -> list[dict]:
    """Cases assigned to me (agent) or to my team (team lead) on the snapshot date, most overdue first."""
    end = f"TIMESTAMP '{AS_OF.isoformat()}' + INTERVAL 1 DAY"
    who = "a.agent_id = ?" if s.role == "agent" else "a.agent_id IN (SELECT agent_id FROM curated.agents WHERE team_lead_id = ?)"
    cur = con.execute(f"""
        SELECT {CASE_COLS} FROM curated.case_assignment_history a JOIN curated.collections_cases k USING (case_id)
        WHERE {who} AND a.assigned_ts < {end} AND (a.unassigned_ts IS NULL OR a.unassigned_ts >= {end})
        ORDER BY k.current_dpd DESC NULLS LAST, k.case_id LIMIT {limit}""", [s.agent_id])
    return _case_dicts(cur)


def case_row(con, case_id: str) -> dict | None:
    end = f"TIMESTAMP '{AS_OF.isoformat()}' + INTERVAL 1 DAY"
    cur = con.execute(f"""
        SELECT {CASE_COLS} FROM curated.collections_cases k
        LEFT JOIN curated.case_assignment_history a ON a.case_id = k.case_id
             AND a.assigned_ts < {end} AND (a.unassigned_ts IS NULL OR a.unassigned_ts >= {end})
        WHERE k.case_id = ?""", [case_id])
    rows = _case_dicts(cur)
    return rows[0] if rows else None


def latest_case_of(con, customer_id: str) -> str | None:
    row = con.execute("""SELECT c.case_id FROM gold.case_customer c JOIN curated.collections_cases k USING (case_id)
                         WHERE c.golden_customer_id = ? ORDER BY k.case_open_date DESC LIMIT 1""", [customer_id]).fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------ screens

def flags(c: dict) -> str:
    f = [n for n, k in (("hardship", "hardship_flag"), ("dispute", "dispute_flag"), ("cease", "cease_contact_flag"),
                        ("vulnerable", "vulnerable_customer_flag")) if c.get(k)]
    return ", ".join(f)


def money(x) -> str:
    return f"${x:,.2f}" if isinstance(x, (int, float)) else "-"


def show_cases(s: Session) -> None:
    if not s.cases:
        console.print(Panel("No cases assigned on the snapshot date. You can still ask rules questions.",
                            border_style="yellow", box=box.ROUNDED))
        return
    t = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for c in ("#", "case", "product", "bucket", "dpd", "overdue", "promise", "next action", "flags"):
        t.add_column(c, justify="right" if c in ("#", "dpd", "overdue") else "left")
    if s.role == "team_lead":
        t.add_column("agent")
    for i, c in enumerate(s.cases, 1):
        promise = f"{money(c['current_ptp_amount'])} by {c['current_ptp_due_date']}" if c.get("current_ptp_amount") else "-"
        row = [str(i), c["case_id"], c["primary_product"] or "-", c["current_bucket"] or "-", str(c["current_dpd"] or 0),
               money(c["total_overdue"]), promise, c["next_scheduled_action"] or "-", Text(flags(c), style="yellow")]
        t.add_row(*row, *([c["agent_id"] or "-"] if s.role == "team_lead" else []))
    whose = "your cases" if s.role == "agent" else f"your team's cases (first {len(s.cases)})"
    console.print(Panel(t, title=f"{whose} on {AS_OF}   type a number to open one", title_align="left",
                        border_style="blue", box=box.ROUNDED))


def show_case(c: dict, customer_id: str) -> None:
    g = Table.grid(padding=(0, 2))
    rows = [("case", f"{c['case_id']}   ({c['case_status']}, {c['queue'] or '-'} queue)"),
            ("customer", customer_id),
            ("product", f"{c['primary_product']}   bucket {c['current_bucket']}   {c['current_dpd']} days past due"),
            ("overdue", money(c["total_overdue"])),
            ("current promise", f"{money(c['current_ptp_amount'])} by {c['current_ptp_due_date']}" if c.get("current_ptp_amount") else "none"),
            ("next action", c["next_scheduled_action"] or "-"),
            ("last contact", str(c["last_contact_ts"] or "-")),
            ("flags", flags(c) or "none")]
    for k, v in rows:
        g.add_row(Text(k, style="dim"), Text(str(v), style="bold yellow" if k == "flags" and v != "none" else "bold"))
    console.print(Panel(Group(g, Text(""), Text("Ask anything about this customer, e.g. \"what did they agree to on the "
                                                "last call?\"  ·  close  to leave the case", style="dim")),
                        title="open case", title_align="left", border_style="magenta", box=box.ROUNDED))


def show_customer_answer(run, secs: float, sources: bool) -> None:
    r = run.response
    if sources:
        t = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
        for c in ("when", "type", "id", "case", "text"):
            t.add_column(c)
        for i in run.items:
            t.add_row(f"{i.event_ts:%Y-%m-%d %H:%M}", KIND_LABEL.get(i.event_type, i.event_type), i.source_id,
                      i.case_id or "-", i.text.replace("\n", "  ")[:90])
        console.print(Panel(t, title=f"what the model read ({len(run.items)} of {run.total_items} items)",
                            title_align="left", border_style="grey50", box=box.ROUNDED))
    body = [Text(r.answer or "", style="bold")]
    for q in r.evidence:
        body += [Text(""), Text(f"“{q}”", style="italic")]
    if r.citations:
        body += [Text(""), Text("from: " + ";  ".join(f"{KIND_LABEL.get(c.kind, c.kind)} {c.source_id} ({c.source_ts})"
                                                      for c in r.citations), style="dim")]
    if r.coverage:
        body += [Text(r.coverage, style="dim")]
    color = "red" if r.not_found else "green"
    console.print(Panel(Group(*body), title=f"answer from {run.case_id or run.golden_customer_id}'s history"
                        + ("  (not found)" if r.not_found else ""), title_align="left",
                        subtitle=f"confidence {r.confidence}   {secs:.1f}s", subtitle_align="right",
                        border_style=color, box=box.ROUNDED))


def show_policy_answer(run, secs: float, sources: bool) -> None:
    r = run.response
    if sources:
        for sec in run.result.sections:
            console.print(Panel(sec.text.strip(), title=f"sent to model: {sec.doc_id} v{sec.version} {sec.section_id}",
                                title_align="left", border_style="grey50", box=box.ROUNDED))
    body = [Text(r.answer or "", style="bold")]
    for q in r.evidence:
        body += [Text(""), Text(f"“{q}”", style="italic")]
    if r.citations:
        body += [Text(""), Text("from: " + ";  ".join(f"{c.source_id} v{c.version} {c.section}".replace(" None", "")
                                                      for c in r.citations), style="dim")]
    color = "red" if r.not_found else "green"
    console.print(Panel(Group(*body), title="answer from the policy documents" + ("  (not found)" if r.not_found else ""),
                        title_align="left", subtitle=f"confidence {r.confidence}   {secs:.1f}s", subtitle_align="right",
                        border_style=color, box=box.ROUNDED))


# ------------------------------------------------------------------ actions

def open_case(con, s: Session, case_id: str | None = None, customer_id: str | None = None) -> bool:
    if customer_id and not case_id:
        case_id = latest_case_of(con, customer_id)
    gid = customer_id or (resolve_case(con, case_id) if case_id else None)
    if not gid or not can_view_customer(con, user_role=s.role, user_id=s.agent_id, golden_customer_id=gid, as_of=AS_OF):
        console.print(Panel(DENIED, border_style="red", box=box.ROUNDED))
        return False
    s.case_id, s.customer_id = case_id, gid
    c = case_row(con, case_id) if case_id else None
    if c:
        show_case(c, gid)
    return True


def ask_customer(con, s: Session, llm: LLM, question: str) -> None:
    t = time.time()
    try:
        with console.status("reading the customer's history and asking the model ..."):
            run = run_customer(question, s.customer_id, con=con, llm=llm, as_of=AS_OF, user_role=s.role,
                               user_id=s.agent_id, case_id=s.case_id)
    except CustomerAccessDenied as e:
        console.print(Panel(str(e), border_style="red", box=box.ROUNDED))
        return
    show_customer_answer(run, time.time() - t, s.sources)


def ask_policy(index, llm: LLM, s: Session, question: str, con) -> None:
    from trustissues.ask.rag.answer import run_policy

    if index is None:
        console.print(Panel("The policy documents are not indexed yet: run "
                            "`python -m trustissues.ask.rag.policy_ingest`.", border_style="red"))
        return
    t = time.time()
    with console.status("searching the policy documents and asking the model ..."):
        run = run_policy(question, index, llm=llm, as_of=AS_OF, user_role=s.role, user_id=s.agent_id)
    show_policy_answer(run, time.time() - t, s.sources)


def load_policy_index():
    try:
        from trustissues.ask.rag.policy_search import PolicyIndex
        return PolicyIndex.load()
    except (FileNotFoundError, ImportError):
        return None


def main() -> None:
    con, writable = open_db()
    llm = LLM(audit=AuditLog(con) if writable else None)
    index = load_policy_index()
    console.print(Rule("[bold]Maple Bank collections assistant[/]"))
    if not writable:
        console.print("[yellow]the database is busy (another process holds it): answers work, but are not logged[/]")

    s = None
    while s is None:
        try:
            aid = console.input("[bold]your agent ID[/] (e.g. AG-2407): ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        s = sign_in(con, aid) if aid else None
        if s is None and aid:
            console.print("[red]not a collections agent or team lead ID[/]")
    s.cases = load_cases(con, s)
    console.print(f"\nSigned in as [bold]{s.name}[/] ({s.agent_id}, {s.role.replace('_', ' ')}, team {s.team}).  "
                  f"Snapshot date {AS_OF}.  Type [bold]help[/] for examples.\n")
    show_cases(s)

    while True:
        where = f"[magenta]{s.case_id or s.customer_id}[/] " if s.customer_id else ""
        try:
            line = console.input(f"\n{where}[bold cyan]ask>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        word = line.lstrip("/").lower()
        if word in ("quit", "exit", "q", "bye"):
            break
        if word in ("help", "?"):
            console.print(Panel(__doc__.split("Then type plain text:")[1].split("Needs OPENROUTER")[0].strip("\n"),
                                title="how to ask", title_align="left", border_style="blue"))
            continue
        if word in ("cases", "list", "home"):
            show_cases(s)
            continue
        if word in ("close", "back"):
            s.case_id = s.customer_id = None
            console.print("[dim]case closed: questions now go to the policy documents unless you name a case[/]")
            continue
        if word in ("sources", "sources on", "sources off"):
            s.sources = not s.sources if word == "sources" else word.endswith("on")
            console.print(f"[dim]show what the model read: {'on' if s.sources else 'off'}[/]")
            continue

        tg = identify(line, current_case=s.case_id)
        try:
            if tg.path == "open":
                if tg.row is not None:
                    if not 1 <= tg.row <= len(s.cases):
                        console.print(f"[red]there is no case {tg.row} in your list[/]")
                        continue
                    open_case(con, s, case_id=s.cases[tg.row - 1]["case_id"])
                else:
                    open_case(con, s, case_id=tg.case_id, customer_id=tg.customer_id)
            elif tg.path == "customer":
                switch = (tg.case_id and tg.case_id != s.case_id) or \
                         (tg.customer_id and not tg.case_id and tg.customer_id != s.customer_id)
                if switch and not open_case(con, s, case_id=tg.case_id, customer_id=tg.customer_id):
                    continue
                if not s.customer_id:
                    console.print("[yellow]which customer? type a number from your list or a case ID first[/]")
                    continue
                ask_customer(con, s, llm, tg.question)
            else:
                ask_policy(index, llm, s, tg.question, con)
        except LLMError as e:
            console.print(Panel(f"The language model could not be reached: {e}", title="try again", border_style="red"))
    console.print("bye")


if __name__ == "__main__":
    main()
