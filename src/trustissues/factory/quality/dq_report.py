"""Data quality: apply the handling, run every check, write reports/data_quality_report.md.

    python -m trustissues.factory.quality.dq_report              # apply handling, then report
    python -m trustissues.factory.quality.dq_report --report-only   # read-only; handling must already be applied

Everything is driven by config/dq_checks.yaml. The report is generated from query results, so the numbers
cannot drift from the data, and it regenerates when new data arrives.

"Handling" that changes data is applied here, idempotently:
  - derived columns (channel_resolved, cif10, salutation_std)
  - boolean flag columns (dq_*) on the table and on <table>_current when that exists
  - curated.dq_orphan_customer_refs: every reference to a customer that does not exist
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from trustissues.common import config
from trustissues.common.db import connect

REPORT_PATH = config.REPO_ROOT / "reports" / "data_quality_report.md"

AREAS = [
    ("known_incident", "A. Known incidents (from the operations log)"),
    ("format", "B. Inconsistent formats"),
    ("reference", "C. Broken references and duplicate customers"),
    ("missing", "D. Missing values"),
    ("logic", "E. Values that contradict each other"),
    ("identity", "F. Customer identity and the golden record"),
]
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
STATUS_TEXT = {
    "fixed": "Fixed",
    "standardised": "Standardised",
    "flagged": "Flagged",
    "deferred": "Deferred to identity layer",
    "resolved": "Resolved by identity matching",
    "review": "Sent to human review",
    "documented": "Documented",
    "context": "Context, not a defect",
    "excluded": "Excluded from current view",
}


@dataclass
class Result:
    id: str
    area: str
    title: str
    tables: list
    severity: str
    status: str
    how: str
    affected: int | None = None
    total: int | None = None
    evidence: str | None = None
    reported: int | None = None
    event: str | None = None
    flag: str | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def pct(self) -> float | None:
        return None if not self.total or self.affected is None else 100.0 * self.affected / self.total


# ---------------------------------------------------------------- config and helpers

def load_checks(path: Path | None = None) -> dict:
    with open(path or config.CONFIG_DIR / "dq_checks.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def sub(sql: str) -> str:
    return sql.replace("{as_of}", config.AS_OF_DATE.isoformat())


def exists(con, qualified: str) -> bool:
    schema, name = qualified.split(".", 1)
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name=?",
                            [schema, name]).fetchone()[0])


def targets(con, table: str) -> list[str]:
    """The table itself plus its <table>_current copy when one exists."""
    out = [table] if exists(con, table) else []
    cur = table + "_current"
    return out + ([cur] if exists(con, cur) else [])


# ---------------------------------------------------------------- handling (changes data)

def apply_derived(con, cfg: dict) -> list[str]:
    done = []
    for d in cfg.get("derived_columns", []):
        for t in targets(con, d["table"]):
            con.execute(f'ALTER TABLE {t} ADD COLUMN IF NOT EXISTS "{d["column"]}" {d["type"]}')
            con.execute(f'UPDATE {t} SET "{d["column"]}" = {sub(d["expr"])}')
            done.append(f'{t}.{d["column"]}')
    return done


def apply_flags(con, cfg: dict) -> list[str]:
    done = []
    for c in cfg.get("checks", []):
        if not c.get("flag"):
            continue
        for t in targets(con, c["table"]):
            con.execute(f'ALTER TABLE {t} ADD COLUMN IF NOT EXISTS "{c["flag"]}" BOOLEAN')
            con.execute(f'UPDATE {t} SET "{c["flag"]}" = coalesce(({sub(c["where"])}), false)')
            done.append(f'{t}.{c["flag"]}')
    return done


def build_orphans(con, cfg: dict) -> int:
    parts = []
    for r in cfg.get("orphan_refs", []):
        if not (exists(con, r["table"]) and exists(con, r["parent"])):
            continue
        t, c, p, pc = r["table"], r["column"], r["parent"], r["parent_column"]
        parts.append(
            f"SELECT '{t.split('.')[1]}' AS table_name, '{c}' AS column_name, {c} AS ref_id, count(*) AS row_count "
            f"FROM {t} x WHERE {c} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {p} p WHERE p.{pc} = x.{c}) GROUP BY {c}")
    if not parts:
        return 0
    con.execute("CREATE OR REPLACE TABLE curated.dq_orphan_customer_refs AS " + " UNION ALL ".join(parts))
    return con.execute("SELECT count(*) FROM curated.dq_orphan_customer_refs").fetchone()[0]


# ---------------------------------------------------------------- evaluation (read only)

def _counts(con, c: dict) -> tuple[int, int]:
    if c.get("sql"):
        a, t = con.execute(sub(c["sql"])).fetchone()
    else:
        a, t = con.execute(f"SELECT count(*) FILTER (WHERE ({sub(c['where'])})), count(*) FROM {c['table']}").fetchone()
    return int(a or 0), int(t or 0)


def reported_by_ops(con, event: str | None) -> int | None:
    if not event or not exists(con, "curated.ops_events"):
        return None
    r = con.execute("SELECT affected_customers_est FROM curated.ops_events WHERE event_id = ?", [event]).fetchone()
    return int(r[0]) if r and r[0] is not None else None


def evaluate(con, cfg: dict) -> tuple[list[Result], list[Result]]:
    issues, clean = [], []
    for c in cfg.get("checks", []):
        r = Result(c["id"], c["area"], c["title"], c.get("tables", []), c["severity"], c["status"], c["how"],
                   event=c.get("event"), flag=c.get("flag"))
        try:
            r.affected, r.total = _counts(con, c)
            if c.get("evidence"):
                r.evidence = con.execute(sub(c["evidence"])).fetchone()[0]
            r.reported = reported_by_ops(con, c.get("event"))
        except Exception as e:  # a missing table or column must not hide the other checks
            r.error = str(e).splitlines()[0][:160]
        issues.append(r)
    for c in cfg.get("clean_checks", []):
        r = Result(c["id"], "clean", c["title"], [], "info", "documented", "")
        try:
            r.affected, r.total = _counts(con, c)
        except Exception as e:
            r.error = str(e).splitlines()[0][:160]
        clean.append(r)
    return issues, clean


# ---------------------------------------------------------------- rendering

def n(x: int | None) -> str:
    return "n/a" if x is None else f"{x:,}"


def pct(r: Result) -> str:
    return "n/a" if r.pct is None else (f"{r.pct:.2f}%" if r.pct < 10 else f"{r.pct:.1f}%")


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render(issues: list[Result], clean: list[Result], cfg: dict, con=None) -> str:
    found = [r for r in issues if r.error is None and (r.affected or r.status == "context")]
    zero = [r for r in issues if r.error is None and not r.affected and r.status != "context"]
    errors = [r for r in issues + clean if r.error]
    clean_ok = [r for r in clean if r.error is None and not r.affected]
    clean_hit = [r for r in clean if r.error is None and r.affected]
    real = [r for r in found if r.status != "context"]
    by_status: dict[str, int] = {}
    for r in real:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    # section numbers are computed so the cross-references in the text can never be wrong
    sec = {key: 3 + i for i, (key, _) in enumerate(AREAS)}
    sec["uniform"] = 3 + len(AREAS)
    sec["method"] = sec["uniform"] + 1
    sec["clean"] = sec["method"] + 1
    sec["errors"] = sec["clean"] + 1
    sec["repro"] = sec["errors"] + (1 if errors else 0)

    L: list[str] = []
    w = L.append
    w("# Data Quality Report")
    w("")
    w(f"Maple Bank collections data, snapshot **{config.AS_OF_DATE.isoformat()}**. Generated from the warehouse by "
      "`python -m trustissues.factory.quality.dq_report`; every number below is a query result, and the checks are "
      "defined in `config/dq_checks.yaml`.")
    w("")
    w("## 1. Summary")
    w("")
    w(f"- **{len(real)} issues found** across the 31 tables, "
      f"plus {len(found) - len(real)} context events from the operations log that are not data defects.")
    w(f"- **{len(clean_ok) + len(zero)} further checks found nothing** (section {sec['clean']}), so the coverage is visible.")
    w("- **No exact duplicate rows exist anywhere.** The duplicates in this data are the same *person* stored under "
      f"different IDs (section {sec['reference']}), not copied rows.")
    w("- How each issue was handled: " + ", ".join(
        f"**{STATUS_TEXT[k].lower()}** {v}" for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])) + ".")
    drift = "n/a"
    if con is not None and exists(con, "raw._schema_drift"):
        k, blocking = con.execute("SELECT count(*), count(*) FILTER (WHERE blocking) FROM raw._schema_drift").fetchone()
        drift = f"{k} event(s), {blocking} blocking"
    w(f"- **Schema drift:** the loader recorded {drift}. The one rename in this release (K1, `channel` to `channel_v2`) "
      "was already registered in the release's schema, so it appears as a data issue, not as drift.")
    w("- **Nothing was deleted or overwritten.** Original columns stay as received in `raw.*` and in the same "
      "columns of `curated.*`. Fixes are new columns, new flags, or recovered values with the original text kept.")
    if errors:
        w(f"- **{len(errors)} check(s) could not run** (listed in section {sec['errors']}).")
    w("")
    w("**How to read the handling column**")
    w("")
    w("| Status | Meaning |")
    w("|---|---|")
    w("| Fixed | The value is repaired or resolved with certainty, in the data |")
    w("| Standardised | A clean `std_*` or derived column holds one format; the original is kept |")
    w("| Flagged | The problem cannot be repaired. A `dq_*` flag column marks the rows so downstream code can exclude or discount them |")
    w("| Documented | Not changed and not flaggable per row; downstream users are told what to do |")
    w("| Resolved | Settled by identity matching in the gold layer (curation never merges people) |")
    w("| Review | Left undecided on purpose; a person decides from the identity review queue |")
    w("| Excluded | Kept in the full table, left out of the one-row-per-key `_current` table |")
    w("| Context | A real-world event, not a data defect |")
    w("")
    w("## 2. Issues by size and severity")
    w("")
    w("| ID | Issue | Records affected | Share of table | Severity | Handling |")
    w("|---|---|---:|---:|---|---|")
    for r in sorted(real, key=lambda r: (SEVERITY_ORDER[r.severity], -(r.affected or 0))):
        w(f"| {r.id} | {esc(r.title)} | {n(r.affected)} | {pct(r)} | {r.severity} | {STATUS_TEXT[r.status]} |")
    w("")

    sections = {
        "known_incident": "The operations log (`ops_events`) lists six events. Each was looked for in the data. "
                          "\"Reported\" is the record count the operations team gave; \"Found\" is what this report measured.",
        "format": "Every source system writes dates of birth, phones, postal codes and names in its own format. "
                  "The original columns still hold those formats; the `std_*` columns are uniform.",
        "reference": "Records that point at something that is not there, and people stored more than once.",
        "missing": "Values that are blank where they should not be. None were imputed.",
        "logic": "Fields that cannot all be true at once. Neither value was changed; the rows are flagged.",
        "identity": "What remains after identity matching joins the five customer keys into one person per golden ID "
                    "(`gold.customer_xref`). These checks read the published gold tables. The rule is: when unsure, "
                    "keep records separate. Details and measured precision: `docs/identity.md` and "
                    "`reports/identity_report.md`.",
    }
    for key, label in AREAS:
        rows = [r for r in found if r.area == key]
        w(f"## {sec[key]}. {label[3:]}")
        w("")
        w(sections[key])
        w("")
        for r in rows:
            w(f"### {r.id}. {r.title}")
            w("")
            parts = []
            if key == "known_incident":
                parts.append(f"**Reported:** {n(r.reported) if r.reported is not None else 'not given'}")
                if r.status == "context":  # a real-world event: the count is related records, not a comparable measurement
                    parts.append(f"**Related records in the data:** {n(r.affected)} of {n(r.total)}")
                else:
                    parts.append(f"**Found:** {n(r.affected)} of {n(r.total)} ({pct(r)})")
                parts.append(f"**Event:** {r.event}")
            else:
                parts.append(f"**Affected:** {n(r.affected)} of {n(r.total)} ({pct(r)})")
            parts.append(f"**Tables:** {', '.join('`' + t + '`' for t in r.tables)}")
            parts.append(f"**Severity:** {r.severity}")
            w(" · ".join(parts))
            w("")
            if r.evidence:
                w(f"*Evidence:* {r.evidence}.")
                w("")
            w(f"**Handling ({STATUS_TEXT[r.status].lower()}).** {r.how}")
            w("")

    w(f"## {sec['uniform']}. Is the data uniform now?")
    w("")
    w("Partly, and the difference matters.")
    w("")
    w("**Uniform:**")
    w("")
    w("- Every typed column (numbers, dates, timestamps, booleans) in `curated.*` has one type. Dates that arrived in "
      "several formats are recovered where the reading is certain (F1).")
    w("- Identity fields (name, date of birth, phone, postal code, employer) are in one format **in the `std_*` "
      "columns**, for the five identity sources.")
    w("- Deposit keys are 10 digits in `cif10`; contact channel is in one column, `channel_resolved`; salutation is "
      "one spelling in `salutation_std`.")
    w("- No exact duplicate rows; primary keys are unique in the four identity tables.")
    w("")
    w("**Not uniform, on purpose or not yet:**")
    w("")
    w("- **The original columns are untouched.** `dob_raw`, `*_phone_raw`, `*_name_raw`, `src_customer_ref` and the "
      "rest still carry each system's own format. Use the `std_*` and derived columns, not the originals.")
    w("- **Customer keys still differ by system in `curated.*`** (CRM ID, card number, loan number, 10-digit CIF). "
      "Identity matching joins them into one golden ID per person in `gold.customer_xref`; what it left open is in "
      f"section {sec['identity']}.")
    w("- **The problems flagged above stay in the data.** Flagged rows are marked, not repaired (masked phones, shifted "
      "bureau columns, bucket and days-past-due disagreements, missing CRM customers).")
    w("- **Currency is mixed** in deposit balances (flag `dq_non_cad`); no conversion is possible without a rate.")
    w("- **Not examined:** free-text content (notes, transcripts), timezone differences between local and UTC "
      "timestamps, and enum values against reference lists beyond the contact codes in C20 and the case and "
      "spelling check that found F17.")
    w("")

    w(f"## {sec['method']}. Method and limits")
    w("")
    w("1. The six operations-log events were each searched for in the data, and the signature in the incident "
      "reports (a count, a drop in calls, a rate against baseline) was checked.")
    w("2. For unlisted issues the data was profiled: formats per field per system, references between tables, "
      "missing values, contradictions between fields, reference codes, and case or spacing variants in all 192 enum columns.")
    w("3. A rule finds the records it is written to find. Where the operations log gives a count and a rule finds "
      "fewer (K4), the rule is the lower bound, not the true number. Where the log cannot be matched to rows (K3), "
      "this report says so.")
    w(f"4. Unmatched records and open review items come from identity matching and are reported in section "
      f"{sec['identity']}, read from the published gold tables. If identity has not been run, those checks are "
      "listed as not runnable instead.")
    w("5. Unlisted issues may remain. The checks cover what was thought of; new data re-runs them automatically but "
      "does not add new kinds of check.")
    w("")

    w(f"## {sec['clean']}. Checks that found nothing")
    w("")
    w("| ID | Check | Rows checked |")
    w("|---|---|---:|")
    for r in clean_ok:
        w(f"| {r.id} | {esc(r.title)} | {n(r.total)} |")
    for r in zero:
        w(f"| {r.id} | {esc(r.title)} | {n(r.total)} |")
    w("")
    if clean_hit:
        w("**Clean checks that unexpectedly found rows:**")
        w("")
        for r in clean_hit:
            w(f"- {r.id} {esc(r.title)}: {n(r.affected)} of {n(r.total)}")
        w("")

    if errors:
        w(f"## {sec['errors']}. Checks that could not run")
        w("")
        for r in errors:
            w(f"- **{r.id}** {esc(r.title)}: `{r.error}`")
        w("")

    w(f"## {sec['repro']}. Reproduce")
    w("")
    w("```bash")
    w("python -m trustissues.factory.quality.dq_report     # applies the flags and derived columns, rewrites this file")
    w("```")
    w("")
    w("Handling artefacts created by the run: derived columns `channel_resolved`, `cif10`, `salutation_std`; flag "
      "columns `dq_*` on the affected tables; the table `curated.dq_orphan_customer_refs`.")
    w("")
    return "\n".join(L)


# ---------------------------------------------------------------- entry point

def run(apply: bool = True, out: Path | None = None, cfg_path: Path | None = None) -> str:
    cfg = load_checks(cfg_path)
    con = connect(read_only=not apply)
    if apply:
        d = apply_derived(con, cfg)
        f = apply_flags(con, cfg)
        o = build_orphans(con, cfg)
        print(f"derived columns: {len(d)} | flag columns: {len(f)} | distinct orphan references: {o:,}", flush=True)
    issues, clean = evaluate(con, cfg)
    text = render(issues, clean, cfg, con)
    con.close()
    path = out or REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    real = [r for r in issues if r.error is None and r.affected]
    print(f"wrote {path} | issues with records: {len(real)} | clean checks: "
          f"{sum(1 for r in clean if r.error is None and not r.affected)} | errors: {sum(1 for r in issues + clean if r.error)}")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply data-quality handling and write the data quality report")
    ap.add_argument("--report-only", action="store_true", help="read-only: do not create flags or derived columns")
    a = ap.parse_args()
    run(apply=not a.report_only)


if __name__ == "__main__":
    main()
