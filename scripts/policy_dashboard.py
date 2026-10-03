"""Terminal dashboard for the policy question path. Type a question, see what the system did and why.

    python scripts/policy_dashboard.py

Inside the dashboard, anything that does not start with "/" is a question. Commands:
    /asof 2025-06-01     the date the rule must apply on (default: the snapshot date)
    /lang auto|EN|FR     language of the answer (auto = detected from the question)
    /province auto|none|QC|ON|...   province the question is about (auto = detected from the question)
    /hardship on|off     also read the hardship_programs table (switches itself off if the database is busy)
    /sources on|off      show the full text of every section sent to the model
    /status              show the current settings        /help    this list        /quit    leave

Needs OPENROUTER_API_KEY (environment or the gitignored .env) and warehouse/rag/policy_chunks.jsonl.
"""
from __future__ import annotations

import readline  # noqa: F401  (arrow keys and history in the prompt)
import time
from datetime import date

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from trustissues.ask.rag.answer import run_policy
from trustissues.ask.rag.policy_search import PolicyIndex
from trustissues.common import config
from trustissues.common.db import connect
from trustissues.common.llm import LLM, LLMError

console = Console()
HELP = __doc__.split("Inside the dashboard,")[1].split("Needs OPENROUTER")[0]


class State:
    def __init__(self):
        self.as_of: date = config.AS_OF_DATE
        self.lang: str | None = None  # None = detect
        self.province: str | None = None  # None = detect; "" = apply none
        self.hardship: bool = True
        self.sources: bool = False


def database_available() -> bool:
    try:
        connect(read_only=True).close()
        return True
    except Exception:
        return False


def settings_panel(st: State, llm: LLM) -> Panel:
    t = Table.grid(padding=(0, 2))
    rows = [("as of", st.as_of.isoformat()), ("answer language", st.lang or "auto (from the question)"),
            ("province", "auto (from the question)" if st.province is None else (st.province or "none")),
            ("hardship table", "on" if st.hardship else "off"), ("show sources", "on" if st.sources else "off"),
            ("model", llm.models.get("answer", "?"))]
    for k, v in rows:
        t.add_row(Text(k, style="dim"), Text(v, style="bold"))
    return Panel(t, title="settings", title_align="left", border_style="blue", box=box.ROUNDED)


def show_run(question: str, run, secs: float, st: State) -> None:
    r, found = run.response, run.result
    how = [f"language {run.language}" + ("" if st.lang is None else " (forced)"),
           f"province {run.province or 'none'}", f"as of {run.result.as_of_date}"]
    head = Text(" | ".join(how), style="dim")
    if run.query != question:
        head = Group(head, Text(f"searched: {run.query}", style="dim italic"))
    console.print(Panel(Group(Text(question, style="bold"), head), title="question", title_align="left",
                        border_style="cyan", box=box.ROUNDED))

    t = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for c in ("score", "document", "version", "section", "lang", "note"):
        t.add_column(c, justify="right" if c == "score" else "left")
    for s in found.sections:
        t.add_row(f"{s.score:.1f}" if not s.pinned else "-", s.doc_id, s.version, s.section_id, s.language,
                  "always included for this province" if s.pinned else "")
    for h in found.hardship_rows:
        t.add_row("-", "hardship_programs", h["version"], h["program_id"], "-", "table row (authoritative)")
    console.print(Panel(t, title=f"retrieved  ({found.candidates_considered} sections eligible on that date)",
                        title_align="left", border_style="yellow", box=box.ROUNDED))

    if st.sources:
        for s in found.sections:
            console.print(Panel(s.text.strip(), title=f"sent to model: {s.doc_id} v{s.version} {s.section_id}",
                                title_align="left", border_style="grey50", box=box.ROUNDED))

    color = "red" if r.not_found else "green"
    body = [Text(r.answer or "", style="bold")]
    for q in r.evidence:
        body += [Text(""), Text(f"“{q}”", style="italic")]
    if r.citations:
        body += [Text(""), Text("cited: " + ";  ".join(
            f"{c.source_id} v{c.version} {c.section}".replace(" None", "") for c in r.citations), style="dim")]
    console.print(Panel(Group(*body), title=f"answer{'  (not found)' if r.not_found else ''}", title_align="left",
                        subtitle=f"confidence {r.confidence}   {secs:.1f}s", subtitle_align="right",
                        border_style=color, box=box.ROUNDED))


def command(line: str, st: State, llm: LLM) -> bool:
    """Apply a /command. Returns False to leave the dashboard."""
    cmd, *arg = line[1:].split()
    val = arg[0] if arg else ""
    try:
        if cmd in ("quit", "exit", "q"):
            return False
        if cmd == "help":
            console.print(Panel(HELP.strip("\n"), title="commands", title_align="left", border_style="blue"))
        elif cmd == "status":
            console.print(settings_panel(st, llm))
        elif cmd == "asof":
            st.as_of = date.fromisoformat(val)
        elif cmd == "lang":
            st.lang = None if val.lower() == "auto" else val.upper() if val.upper() in ("EN", "FR") else 1 / 0
        elif cmd == "province":
            st.province = None if val.lower() == "auto" else "" if val.lower() == "none" else val.upper()
        elif cmd == "hardship":
            st.hardship = val.lower() == "on"
            if st.hardship and not database_available():
                st.hardship = False
                console.print("[yellow]the database is busy (another process holds it), hardship table stays off[/]")
        elif cmd == "sources":
            st.sources = val.lower() == "on"
        else:
            console.print(f"[red]unknown command /{cmd}[/]  (try /help)")
            return True
        if cmd not in ("help", "status"):
            console.print(settings_panel(st, llm))
    except (ValueError, ZeroDivisionError):
        console.print(f"[red]could not read '{line}'[/]  (try /help)")
    return True


def main() -> None:
    try:
        ix, llm = PolicyIndex.load(), LLM()
    except FileNotFoundError:
        raise SystemExit("run `python -m trustissues.ask.rag.policy_ingest` first")
    st = State()
    if not database_available():
        st.hardship = False
        console.print("[yellow]the database is busy (curation running?): hardship table is off, "
                      "answers use the documents only. /hardship on to retry.[/]")
    console.print(Rule("[bold]Maple Bank policy assistant[/]  (type a question, or /help)"))
    console.print(settings_panel(st, llm))
    while True:
        try:
            line = console.input("\n[bold cyan]ask>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.startswith("/"):
            if not command(line, st, llm):
                break
            continue
        t = time.time()
        try:
            with console.status("searching the documents and asking the model ..."):
                run = run_policy(line, ix, llm=llm, as_of=st.as_of, user_role="agent", language=st.lang,
                                 province=st.province, with_hardship=st.hardship)
        except LLMError as e:
            console.print(Panel(str(e), title="the model call failed", border_style="red"))
            continue
        show_run(line, run, time.time() - t, st)
    console.print("bye")


if __name__ == "__main__":
    main()
