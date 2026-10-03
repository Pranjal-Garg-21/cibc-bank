"""Load config/settings.yaml. Single place for AS_OF_DATE, paths and thresholds."""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"


@lru_cache(maxsize=1)
def settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else REPO_ROOT / path


AS_OF_DATE: date = date.fromisoformat(settings()["as_of_date"])


def maple_data_dir() -> Path:
    return _resolve(os.environ.get("MAPLE_DATA_DIR", settings()["paths"]["maple_data_dir"]))


def warehouse_path() -> Path:
    base = _resolve(os.environ.get("WAREHOUSE_DIR", settings()["paths"]["warehouse_dir"]))
    base.mkdir(parents=True, exist_ok=True)
    return base / settings()["paths"]["duckdb_file"]


def p3_dir() -> Path:
    """P3 outputs (Parquet, model files). Separate from the DuckDB file so P3 never takes the write lock."""
    base = _resolve(os.environ.get("P3_OUTPUT_DIR", settings()["p3"]["output_dir"]))
    base.mkdir(parents=True, exist_ok=True)
    return base


def duckdb_limits() -> dict:
    """Memory and thread caps for DuckDB connections (settings.yaml `duckdb:`, env overrides)."""
    d = settings().get("duckdb", {})
    return {"memory_limit": os.environ.get("DUCKDB_MEMORY_LIMIT", d.get("memory_limit", "10GB")),
            "threads": int(os.environ.get("DUCKDB_THREADS", d.get("threads", 8)))}
