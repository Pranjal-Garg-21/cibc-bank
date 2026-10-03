"""DuckDB connection factory. Schemas: raw -> curated -> identity (matching work tables) -> gold_stage (built, not yet checked)
-> gold (published only after the quality gate passes). Consumers read only gold."""
from __future__ import annotations

import duckdb

from .config import warehouse_path

SCHEMAS = ("raw", "curated", "identity", "gold_stage", "gold")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(warehouse_path()), read_only=read_only)
    if not read_only:
        for s in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    return con
