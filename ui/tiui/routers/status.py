"""Data status: the last publish attempt, the contract rule results, gold row counts and the reports (read-only)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from ..deps import AS_OF, current_user, warehouse
from ..errors import ApiError
from ..serialize import jsonable

router = APIRouter(prefix="/api/status", tags=["status"])

GOLD_TABLES = ("c360_customer", "c360_account", "customer_xref", "decisioning_customer", "promises_enriched",
               "customer_safeguards", "customer_timeline", "identity_review_queue", "nba_decisions", "nba_overrides")
REPORT_NAME = re.compile(r"^[a-z0-9_]+\.md$")


def _reports(reports_dir):
    out = []
    for p in sorted(reports_dir.glob("*.md")):
        title = next((ln.lstrip("# ").strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("#")),
                     p.stem)
        out.append({"name": p.name, "title": title,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


@router.get("")
def status(request: Request):
    s = request.app.state.settings
    with warehouse(s) as con:
        current_user(request, con)
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold'").fetchall()}
        publish = con.execute("""SELECT attempt_ts, published, failed_rules, warned_rules, secs FROM gold._publish_log
                                 ORDER BY attempt_ts DESC LIMIT 1""").fetchone() if "_publish_log" in tables else None
        rules = con.execute("""SELECT rule_id, title, blocking, violations, total, passed FROM gold._contract_results
                               ORDER BY rule_id""").fetchall() if "_contract_results" in tables else []
        counts = {t: con.execute(f"SELECT count(*) FROM gold.{t}").fetchone()[0] for t in GOLD_TABLES if t in tables}
    return jsonable({
        "as_of_date": AS_OF,
        "publish": dict(zip(("attempt_ts", "published", "failed_rules", "warned_rules", "secs"), publish))
        if publish else None,
        "rules": [dict(zip(("rule_id", "title", "blocking", "violations", "total", "passed"), r)) for r in rules],
        "row_counts": counts,
        "reports": _reports(s.reports_dir),
    })


@router.get("/reports/{name}")
def report(name: str, request: Request):
    s = request.app.state.settings
    with warehouse(s) as con:
        current_user(request, con)
    path = s.reports_dir / name
    if not REPORT_NAME.match(name) or not path.is_file() or path.resolve().parent != s.reports_dir.resolve():
        raise ApiError(404, "not_found", "No such report.")
    return {"name": name, "markdown": path.read_text(encoding="utf-8")}
