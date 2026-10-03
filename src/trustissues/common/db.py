"""DuckDB connection factory. Schemas: raw -> curated -> gold. Consumers read only gold."""
from __future__ import annotations

import duckdb

from .config import settings, warehouse_path

SCHEMAS = ("raw", "curated", "gold")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(warehouse_path()), read_only=read_only)
    cfg = settings().get("duckdb", {})  # resource caps, so a big build spills to disk instead of being killed
    if cfg.get("memory_limit"):
        con.execute(f"SET memory_limit='{cfg['memory_limit']}'")
    if cfg.get("threads"):
        con.execute(f"SET threads={int(cfg['threads'])}")
    if cfg.get("preserve_insertion_order") is False:
        con.execute("SET preserve_insertion_order=false")
    if not read_only:
        for s in SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    return con
