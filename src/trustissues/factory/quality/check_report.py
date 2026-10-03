"""Render quality results without opening a database or fabricating completed checks."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from .ops_anomaly import Anomaly
from .contract_checks import CheckResult, publish_allowed


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(checks: Iterable[CheckResult], anomalies: Iterable[Anomaly] = (), *,
                  generated_at: datetime, issues: Iterable[str] = ()) -> str:
    checks = list(checks)
    lines = ["# Data quality report", "", f"Generated: {generated_at.isoformat()}", "",
             f"Publish gate: {'PASS' if publish_allowed(checks) else 'BLOCKED'}", "",
             "| Rule | Severity | Status | Affected | Detail |", "|---|---|---|---|---|"]
    for r in checks:
        lines.append(f"| {r.rule_id}: {_cell(r.name)} | {'block' if r.blocking else 'warn'} | {r.status} | "
                     f"{r.affected_rows if r.affected_rows is not None else 'unknown'} | {_cell(r.detail)} |")
    lines += ["", "## Anomalies", "", "| Source | Metric | Value | Status | Incident evidence |", "|---|---|---|---|---|"]
    for a in anomalies:
        lines.append(f"| {_cell(a.observation.source)} | {_cell(a.observation.metric)} | {a.observation.value} | "
                     f"{a.status} | {_cell(', '.join(a.event_ids))} |")
    lines += ["", "Incident overlap is supporting evidence, not proof of causation.", "", "## Open issues", ""]
    lines.extend(f"- {_cell(issue)}" for issue in issues)
    return "\n".join(lines) + "\n"


def write_report(path: Path | str, **kwargs) -> None:
    Path(path).write_text(render_report(**kwargs), encoding="utf-8")
