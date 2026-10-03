"""Run the data contract (contracts/c360_customer.yaml) against a schema. Drives the publish gate.

    python -m trustissues.factory.quality.contract_checks              # checks gold_stage
    python -m trustissues.factory.quality.contract_checks --schema gold

Rules are SQL returning (violations, total), except two kinds computed here:
  decisioning_allow_list  no column of gold.decisioning_customer is protected, direct PII or a proxy anywhere in
                          config/sensitivity.yaml, or in the rule's forbidden_extra list
  interface_shape         every P1 table carries the columns and types agreed in contracts/interfaces/stubs.sql
  semantic_shape          every gold.semantic_* table has exactly the columns and types in P2's ask/data/semantic.py

Plain SQL, not Great Expectations as the plan suggested: the rules already are SQL, and one fewer dependency
keeps the gate fast and easy to read. Results go to <schema>._contract_results.

One gate. P2 built eight checks of its own (run_checks); they repeated Q1-Q9 one for one, so at the P2 merge
(4 Oct) the two they added were folded into the YAML (overdue totals in Q7, build age as Q10) and the second runner
was removed (merge.md §6).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import yaml

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.common.interfaces import STUBS_SQL

CONTRACT = config.REPO_ROOT / "contracts" / "c360_customer.yaml"
# the P1 tables published from gold_stage; other stub tables (text_features, nba_*, audit_log) belong to other owners
P1_TABLES = ["customer_xref", "c360_customer", "c360_account", "decisioning_customer", "promises_enriched",
             "customer_safeguards", "bucket_transitions", "customer_timeline"]


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


def load_contract(path: Path | str = CONTRACT) -> dict:
    """Load the published contract by default, or an explicit contract path."""
    with open(path, encoding="utf-8") as f:
        contract = yaml.safe_load(f)
    if not isinstance(contract, dict):
        raise ValueError("Contract YAML must be a mapping")
    return contract


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


def semantic_shape(con, schema: str) -> list[str]:
    """P2's validator allows exactly the columns in SCHEMAS, so a semantic table must match them: none missing, none extra."""
    from trustissues.ask.data.semantic import SCHEMAS

    actual = {}
    for t, c, d in con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                               "WHERE table_schema = ? AND table_name LIKE 'semantic\\_%' ESCAPE '\\'", [schema]).fetchall():
        actual.setdefault(t, {})[c] = d
    problems = []
    for qualified, cols in SCHEMAS.items():
        t = qualified.split(".", 1)[1]
        have = actual.get(t, {})
        problems += [f"{t}.{c}: expected {d}, found {have.get(c, 'missing')}" for c, d in cols.items() if have.get(c) != d]
        problems += [f"{t}.{c}: not in the contract" for c in have if c not in cols]
    return problems


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
            elif kind == "semantic_shape":
                from trustissues.ask.data.semantic import SCHEMAS
                problems = semantic_shape(con, schema)
                results.append(RuleResult(rule["id"], rule["title"], rule["blocking"], len(problems), len(SCHEMAS),
                                          "; ".join(problems[:10])))
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
