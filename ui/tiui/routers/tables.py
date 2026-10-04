"""Read-only warehouse table browser for team leads in the local demo."""
from __future__ import annotations

import re
from functools import lru_cache

import yaml
from fastapi import APIRouter, Query, Request

from trustissues.common import config

from ..deps import current_user, require_role, warehouse
from ..errors import ApiError
from ..security import sensitive_columns
from ..serialize import jsonable

router = APIRouter(prefix="/api/tables", tags=["tables"])
NAME = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
SCHEMAS = ("curated", "gold")
EXCLUDED = frozenset({("gold", "audit_log")})
SAFE_IDS = frozenset({"golden_customer_id", "recommendation_id"})


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _viewer(request, con):
    user = current_user(request, con)
    require_role(user, ("team_lead",))
    if not request.app.state.settings.demo:
        raise ApiError(403, "forbidden", "Table browsing is available only in the demo environment.")
    return user


def _catalog(con):
    return [(s, t) for s, t in con.execute("""SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema IN ('curated', 'gold') AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name""").fetchall()
        if NAME.fullmatch(t) and (s, t) not in EXCLUDED]


@lru_cache(maxsize=1)
def _classification():
    with (config.CONFIG_DIR / "sensitivity.yaml").open() as handle:
        tables = yaml.safe_load(handle).get("tables", {})
    global_levels: dict[str, set[str]] = {}
    for columns in tables.values():
        for name, level in columns.items():
            global_levels.setdefault(name.lower(), set()).add(level)
    return tables, global_levels


def _visible(table: str, column: str) -> bool:
    if column.lower() in sensitive_columns():
        return False
    tables, global_levels = _classification()
    columns = tables.get(table)
    if columns is not None:
        return columns.get(column) == "none"
    return column in SAFE_IDS or global_levels.get(column.lower()) == {"none"}


@router.get("")
def catalog(request: Request):
    with warehouse(request.app.state.settings) as con:
        _viewer(request, con)
        rows = [{"schema": s, "name": t,
                 "rows": con.execute(f"SELECT count(*) FROM {_quote(s)}.{_quote(t)}").fetchone()[0]}
                for s, t in _catalog(con)]
    return {"tables": rows}


@router.get("/{schema}/{name}")
def rows(schema: str, name: str, request: Request, offset: int = Query(0, ge=0, le=1_000_000),
         limit: int = Query(50, ge=1, le=100)):
    with warehouse(request.app.state.settings) as con:
        user = _viewer(request, con)
        if schema not in SCHEMAS or not NAME.fullmatch(name) or (schema, name) not in _catalog(con):
            raise ApiError(404, "not_found", "No such table is available to browse.")
        columns = [{"name": c, "type": dtype, "masked": not _visible(name, c)} for c, dtype in con.execute(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position""", [schema, name]).fetchall()]
        target = f"{_quote(schema)}.{_quote(name)}"
        total = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
        selected = []
        for c in columns:
            col = _quote(c["name"])
            if c["masked"]:
                expression = f"CASE WHEN {col} IS NULL THEN NULL ELSE '***' END"
            elif c["type"] in ("VARCHAR", "BLOB", "JSON") or '[' in c["type"] or c["type"].startswith(('STRUCT', 'MAP')):
                expression = f"left(CAST({col} AS VARCHAR), 2000)"
            else:
                expression = col
            selected.append(f"{expression} AS {col}")
        data = con.execute(f"SELECT {', '.join(selected)} FROM {target} ORDER BY rowid LIMIT ? OFFSET ?",
                           [limit, offset]).fetchall()
    request.app.state.access_log.record(action="browse_table", user_id=user.agent_id, role=user.role,
                                        object_id=f"{schema}.{name}", request_id=request.state.request_id)
    return jsonable({"schema": schema, "name": name, "columns": columns, "total": total, "offset": offset,
                     "limit": limit, "data": {"columns": [c["name"] for c in columns], "rows": data}})
