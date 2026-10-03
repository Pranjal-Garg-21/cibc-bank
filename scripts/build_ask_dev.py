"""Build the isolated Ask development database: curated copies plus the gold.semantic_* dev views.

    python scripts/build_ask_dev.py            # writes warehouse/ask_dev/trustissues_ask_dev.duckdb

Copies 8 curated tables out of the warehouse (read-only attach) so nothing here can touch the real file.
The name must end `_ask_dev.duckdb`: the adapters refuse any other file database.
"""
from __future__ import annotations

import argparse
import time

import duckdb

from trustissues.common import config
from trustissues.common.interfaces import apply_stubs
from trustissues.ask.data.semantic_adapters import build_dev_adapters

TABLES = ["collections_cases", "agents", "promises_to_pay", "contact_history", "card_accounts", "loan_accounts",
          "deposit_accounts", "account_monthly_snapshot"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory", default="5GB")
    a = ap.parse_args()
    out = config.warehouse_path().parent / "ask_dev" / "trustissues_ask_dev.duckdb"
    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)
    con = duckdb.connect(str(out))
    con.execute(f"SET memory_limit='{a.memory}'")
    con.execute("SET threads=4")
    con.execute(f"ATTACH '{config.warehouse_path()}' AS w (READ_ONLY)")
    con.execute("CREATE SCHEMA curated")
    for t in TABLES:
        t0 = time.time()
        con.execute(f'CREATE TABLE curated."{t}" AS SELECT * FROM w.curated."{t}"')
        print(f"copied {t} in {time.time() - t0:.0f}s", flush=True)
    con.execute("DETACH w")
    apply_stubs(con)
    build_dev_adapters(con, development_database=True)
    con.close()
    print(f"built {out}")


if __name__ == "__main__":
    main()
