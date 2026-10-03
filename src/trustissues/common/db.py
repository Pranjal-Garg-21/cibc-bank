"""DuckDB connection factory. Schemas: raw -> curated -> gold. Consumers read only gold."""
from __future__ import annotations

import duckdb

from .config import warehouse_path

SCHEMAS = ("raw", "curated", "gold")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(warehouse_path()), read_only=read_only)
    if not read_only:
        for s in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    return con
