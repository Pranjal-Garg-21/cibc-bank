"""Run the data contract (contracts/c360_customer.yaml) against a schema. Drives the publish gate.

    python -m trustissues.factory.quality.contract_checks              # checks gold_stage
    python -m trustissues.factory.quality.contract_checks --schema gold

Rules are SQL returning (violations, total), except two kinds computed here:
  decisioning_allow_list  no column of gold.decisioning_customer is protected, direct PII or a proxy anywhere in
                          config/sensitivity.yaml, or in the rule's forbidden_extra list
  interface_shape         every P1 table carries the columns and types agreed in contracts/interfaces/stubs.sql

Plain SQL, not Great Expectations as the plan suggested: the rules already are SQL, and one fewer dependency
keeps the gate fast and easy to read. Results go to <schema>._contract_results.

Two runners since the P2 merge (4 Oct). run() above is the publish gate (gold/publish.py). run_checks() is P2's
eight evidence-based checks over the gold tables, rendered by check_report.py; nothing calls it in the pipeline and
it does not gate publishing. Whether it should, or should be folded into the YAML, is open (merge.md §6).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping

import yaml

from trustissues.ask.data.validator import sensitive_columns
from trustissues.common import config
from trustissues.common.config import CONFIG_DIR
from trustissues.common.db import connect
from trustissues.common.interfaces import STUBS_SQL


CONTRACT = config.REPO_ROOT / "contracts" / "c360_customer.yaml"
# the P1 tables published from gold_stage; other stub tables (text_features, nba_*, audit_log) belong to other owners
P1_TABLES = ["customer_xref", "c360_customer", "c360_account", "decisioning_customer", "promises_enriched",
             "customer_safeguards", "bucket_transitions", "customer_timeline"]


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


def load_contract(path: Path | str = CONTRACT) -> dict:
    """Load the published contract by default, or an explicit contract path."""
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

    Legacy evidence checks use the agreed gold stubs; run() owns the staging publish gate.
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


@dataclass
class RuleResult:
    id: str
    title: str
    blocking: bool
    violations: int
    total: int
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.violations == 0


def forbidden_columns(extra: list[str]) -> set[str]:
    with open(config.CONFIG_DIR / "sensitivity.yaml", encoding="utf-8") as f:
        sens = yaml.safe_load(f)
    names = {c.split(".", 1)[1] for c in sens.get("protected_columns", []) + sens.get("proxy_columns", [])}
    for cols in (sens.get("tables") or {}).values():
        names |= {c for c, level in cols.items() if level in ("direct_pii", "protected")}
    return names | set(extra)


def interface_shape(con, schema: str) -> list[str]:
    import duckdb
    scratch = duckdb.connect(":memory:")
    scratch.execute(STUBS_SQL.read_text(encoding="utf-8"))
    expected = scratch.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                               "WHERE table_schema = 'gold'").fetchall()
    scratch.close()
    actual = {(t, c): d for t, c, d in con.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = ?", [schema]).fetchall()}
    return [f"{t}.{c}: expected {d}, found {actual.get((t, c), 'missing')}"
            for t, c, d in expected if t in P1_TABLES and actual.get((t, c)) != d]


def run(con, schema: str = "gold_stage") -> list[RuleResult]:
    contract = load_contract()
    results = []
    for rule in contract["rules"]:
        kind = rule.get("kind", "sql")
        try:
            if kind == "decisioning_allow_list":
                cols = [r[0] for r in con.execute(f"DESCRIBE {schema}.decisioning_customer").fetchall()]
                bad = sorted(set(cols) & forbidden_columns(rule.get("forbidden_extra", [])))
                results.append(RuleResult(rule["id"], rule["title"], rule["blocking"], len(bad), len(cols), ", ".join(bad)))
            elif kind == "interface_shape":
                problems = interface_shape(con, schema)
                results.append(RuleResult(rule["id"], rule["title"], rule["blocking"], len(problems), len(P1_TABLES),
                                          "; ".join(problems[:10])))
            else:
                sql = rule["sql"].replace("{g}", schema).replace("{as_of}", config.AS_OF_DATE.isoformat())
                v, t = con.execute(sql).fetchone()
                results.append(RuleResult(rule["id"], rule["title"], rule["blocking"], int(v or 0), int(t or 0)))
        except Exception as e:  # a rule that cannot run counts as failed
            results.append(RuleResult(rule["id"], rule["title"], rule["blocking"], 1, 0, f"error: {e}"[:500]))
    con.execute(f"""CREATE OR REPLACE TABLE {schema}._contract_results (rule_id VARCHAR, title VARCHAR, blocking BOOLEAN,
        violations BIGINT, total BIGINT, passed BOOLEAN, detail VARCHAR, checked_ts TIMESTAMP)""")
    con.executemany(f"INSERT INTO {schema}._contract_results VALUES (?,?,?,?,?,?,?, now())",
                    [(r.id, r.title, r.blocking, r.violations, r.total, r.passed, r.detail) for r in results])
    return results


def print_results(results: list[RuleResult]) -> None:
    for r in results:
        status = "pass" if r.passed else ("FAIL (blocking)" if r.blocking else "warn")
        print(f"  {r.id:3s} {status:16s} {r.violations:>10,} / {r.total:<12,} {r.title}" + (f"  [{r.detail}]" if r.detail else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the c360 data contract")
    ap.add_argument("--schema", default="gold_stage")
    con = connect()
    print_results(run(con, ap.parse_args().schema))
    con.close()


if __name__ == "__main__":
    main()
