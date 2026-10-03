"""Execute only validated queries on dedicated read-only or in-memory connections."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Timer
from time import monotonic
from typing import Mapping

import duckdb

from .validator import ValidatedQuery


class QueryTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    elapsed_ms: float


_EXECUTION_LOCK = Lock()  # prevents late interrupts or concurrent cursor use across requests


def execute(query: ValidatedQuery, parameters: Mapping[str, object] | None = None, *,
            database: str | Path | None = None, con=None, timeout_seconds: float = 5.0) -> QueryResult:
    if not isinstance(query, ValidatedQuery):
        raise TypeError("execute requires a ValidatedQuery")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if (con is None) == (database is None):
        raise ValueError("Supply exactly one database path or in-memory connection")
    own = con is None
    if own:
        path = Path(database).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        con = duckdb.connect(str(path), read_only=True)
    elif any(row[2] for row in con.execute("PRAGMA database_list").fetchall()):
        raise ValueError("Injected connections must be in-memory; files use read_only=True")
    args = dict(parameters or {})
    if set(args) & set(query.scope_parameters) or any(k.startswith("__ti_scope_") for k in args):
        if own:
            con.close()
        raise ValueError("Caller cannot override scope parameters")
    args.update(query.scope_parameters)
    timed_out = Event()
    def interrupt():
        timed_out.set()
        con.interrupt()
    with _EXECUTION_LOCK:
        timer = Timer(timeout_seconds, interrupt)
        start = monotonic()
        timer.start()
        try:
            cursor = con.execute(query.sql, args)
            columns = tuple(d[0] for d in cursor.description)
            rows = tuple(cursor.fetchmany(query.row_cap + 1))
            if timed_out.is_set():
                raise QueryTimeout("Query runtime limit exceeded")
            if len(rows) > query.row_cap:
                raise ValueError("Validated query exceeded row cap")
            return QueryResult(columns, rows, (monotonic() - start) * 1000)
        except duckdb.Error as e:
            if timed_out.is_set():
                raise QueryTimeout("Query runtime limit exceeded") from e
            raise
        finally:
            timer.cancel()
            timer.join()
            if own:
                con.close()
