"""Eight design-contract checks, using explicit source evidence and clocks.

Missing evidence is NOT a pass. This DuckDB implementation can be wired to P1's YAML;
it does not create Great Expectations suites or publish gold itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from trustissues.ask.data.validator import sensitive_columns
from trustissues.common.config import CONFIG_DIR


@dataclass(frozen=True)
class CheckResult:
    rule_id: int
    name: str
    blocking: bool
    status: str  # pass | fail | not_checked
    affected_rows: int | None
    detail: str = ""


RULE_NAMES = {
    1: "Golden ID unique and nonempty", 2: "Every source record mapped or flagged",
    3: "Low-confidence automatic links confirmed", 4: "Decisioning contains no sensitive fields",
    5: "Source safety flags propagated", 6: "DOB and age valid", 7: "Totals reconcile to accounts",
    8: "Refresh no more than 24 hours old",
}
SAFETY_FLAGS = ("cease_contact", "deceased", "bankruptcy", "open_dispute")


def publish_allowed(results: Iterable[CheckResult]) -> bool:
    results = list(results)
    return {r.rule_id for r in results} == set(RULE_NAMES) and all(
        r.status == "pass" for r in results if r.blocking)


def load_contract(path: Path | str) -> dict:
    """Read P1's contract without guessing a future YAML shape or overriding rule severity."""
    with open(path, encoding="utf-8") as f:
        contract = yaml.safe_load(f)
    if not isinstance(contract, dict):
        raise ValueError("Contract YAML must be a mapping")
    return contract


def run_checks(con, *, as_of_date: date, checked_at: datetime,
               source_records: Iterable[tuple[str, str]] | None = None,
               source_safety: Mapping[str, Mapping[str, bool]] | None = None,
               sensitivity_path: Path = CONFIG_DIR / "sensitivity.yaml",
               decisioning_allowlist: set[str] | None = None) -> list[CheckResult]:
    """Source inventory is (system, customer key); safety evidence uses golden IDs.

    Call on P1's staging connection before publish; table names currently follow agreed gold stubs.
    checked_at is the batch clock, as_of_date is the dataset clock (used only for DOB checks).
    """
    results = []
    def record(rule, affected, detail="", status=None):
        results.append(CheckResult(rule, RULE_NAMES[rule], rule <= 5,
                                   status or ("fail" if affected else "pass"), affected, detail))
    customers = con.execute("SELECT golden_customer_id,date_of_birth,total_balance,total_overdue,refresh_ts,"
                            "cease_contact,deceased,bankruptcy,open_dispute FROM gold.c360_customer").fetchall()
    ids = [r[0] for r in customers]
    record(1, sum(x is None or not x.strip() for x in ids) + len(ids) - len(set(ids)))
    xref = con.execute("SELECT source_system,source_key,golden_customer_id,match_method,match_confidence,status "
                       "FROM gold.customer_xref").fetchall()
    if source_records is None:
        record(2, None, "Source inventory not supplied", "not_checked")
    else:
        grouped = {}
        for system, key, golden, method, confidence, status in xref:
            grouped.setdefault((system, key), []).append((golden, status))
        failures = 0
        for key in set(source_records) | set(grouped):
            rows = grouped.get(key, [])
            good = len(rows) == 1 and ((rows[0][0] in set(ids) and rows[0][1] in {"linked", "singleton"}) or
                                      rows[0][1] in {"unresolved", "in_review", "blocked_conflict"})
            failures += not good
        record(2, failures)
    record(3, sum(status == "linked" and (confidence is None or not 0 <= confidence <= 1 or
                    (confidence < .95 and method not in {"llm_confirmed", "human"}))
                  for _, _, _, method, confidence, status in xref))
    cols = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns "
                                     "WHERE table_schema='gold' AND table_name='decisioning_customer'").fetchall()}
    forbidden = cols & sensitive_columns(sensitivity_path)
    if decisioning_allowlist is None:
        record(4, None, "Explicit approved decisioning allow-list required; forbidden fields: " +
               ", ".join(sorted(forbidden)), "fail" if forbidden else "not_checked")
    else:
        forbidden |= cols - decisioning_allowlist
        record(4, len(forbidden), ", ".join(sorted(forbidden)))
    if source_safety is None:
        record(5, None, "Source safety evidence not supplied", "not_checked")
    else:
        actual = {r[0]: dict(zip(SAFETY_FLAGS, r[5:])) for r in customers}
        record(5, sum(any(value and actual.get(gid, {}).get(flag) is not True for flag, value in flags.items())
                      for gid, flags in source_safety.items()), "Compare against source OR of safety flags")
    invalid_dob = 0
    for row in customers:
        dob = row[1]
        if dob is None:
            invalid_dob += 1
            continue
        age = as_of_date.year - dob.year - ((as_of_date.month, as_of_date.day) < (dob.month, dob.day))
        invalid_dob += not 18 <= age <= 110
    record(6, invalid_dob)
    account_totals = dict((gid, (balance, overdue)) for gid, balance, overdue in con.execute(
        "WITH latest AS (SELECT * FROM gold.c360_account QUALIFY row_number() OVER "
        "(PARTITION BY golden_customer_id,product,account_id ORDER BY snapshot_date DESC)=1) "
        "SELECT golden_customer_id,sum(balance),sum(overdue_amount) FROM latest GROUP BY golden_customer_id").fetchall())
    record(7, sum(balance is None or overdue is None or account_totals.get(gid, (None, None))[0] is None or
                  account_totals.get(gid, (None, None))[1] is None or
                  abs(balance-account_totals[gid][0]) > .01 or abs(overdue-account_totals[gid][1]) > .01
                  for gid, _, balance, overdue, *_ in customers), "Latest snapshot per product/account; CAD tolerance .01")
    # Empty products are not usable: missing freshness cannot be a vacuous pass.
    record(8, sum(r[4] is None or r[4] > checked_at or checked_at-r[4] > timedelta(hours=24) for r in customers)
           if customers else 1, "Batch clock supplied by caller; dataset as-of date is not the freshness clock")
    return results
