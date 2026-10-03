"""Publish gate: gold_stage -> gold, only if every blocking contract rule passes (plan §3.4).

    python -m trustissues.factory.gold.publish

Everything P1 builds lands in gold_stage first. The contract (contracts/c360_customer.yaml) runs against it; if a
blocking rule fails, nothing is copied and consumers keep reading yesterday's gold. If all pass, every gold_stage
table is copied into gold in one transaction, so readers never see half a publish. Each attempt is logged in
gold._publish_log.
"""
from __future__ import annotations

import time

from trustissues.common.db import connect
from trustissues.common.interfaces import apply_stubs
from trustissues.factory.quality import contract_checks


class PublishBlocked(Exception):
    pass


def publish(con) -> dict:
    t0 = time.time()
    results = contract_checks.run(con, "gold_stage")
    contract_checks.print_results(results)
    failed = [r.id for r in results if r.blocking and not r.passed]
    warned = [r.id for r in results if not r.blocking and not r.passed]
    tables = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold_stage' "
                                        "AND table_name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY 1").fetchall()]
    con.execute("""CREATE TABLE IF NOT EXISTS gold._publish_log (attempt_ts TIMESTAMP, published BOOLEAN, failed_rules VARCHAR,
        warned_rules VARCHAR, tables VARCHAR, secs DOUBLE)""")
    if not failed:
        con.execute("BEGIN TRANSACTION")
        try:
            for t in tables:
                con.execute(f'CREATE OR REPLACE TABLE gold."{t}" AS SELECT * FROM gold_stage."{t}"')
            con.execute("CREATE OR REPLACE TABLE gold._contract_results AS SELECT * FROM gold_stage._contract_results")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        apply_stubs(con)  # create the other owners' stub tables if missing, and re-check every agreed shape
    con.execute("INSERT INTO gold._publish_log VALUES (now(), ?, ?, ?, ?, ?)",
                [not failed, ",".join(failed), ",".join(warned), ",".join(tables), round(time.time() - t0, 1)])
    if failed:
        raise PublishBlocked(f"blocking contract rules failed: {failed}; gold was not changed")
    return {"published": len(tables), "warnings": warned, "secs": round(time.time() - t0)}


def run() -> dict:
    con = connect()
    try:
        return publish(con)
    finally:
        con.close()


def main() -> None:
    print(run())


if __name__ == "__main__":
    main()
