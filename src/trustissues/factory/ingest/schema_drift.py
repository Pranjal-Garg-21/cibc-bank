"""Compare the columns arriving in a source with the registered schema (schema.json / dictionary).

- registered column missing from the data -> BLOCKING (load for that source stops)
- column in the data that nobody registered -> warning, loaded, flagged for the mapping assistant
- a missing+new pair with similar names is reported as a likely rename (an LLM can rank these later)
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

DDL = """
CREATE TABLE IF NOT EXISTS raw._schema_drift (
    detected_at TIMESTAMP, batch_id VARCHAR, source VARCHAR, kind VARCHAR,
    column_name VARCHAR, suggestion VARCHAR, blocking BOOLEAN
)"""


class SchemaDriftError(RuntimeError):
    pass


@dataclass
class Drift:
    source: str
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    renames: dict[str, str] = field(default_factory=dict)  # missing -> likely new name

    @property
    def blocking(self) -> bool:
        return bool(self.missing)


def detect(source: str, expected: list[str] | None, actual: list[str], allow_missing=()) -> Drift:
    d = Drift(source)
    if expected is None:  # unregistered source: nothing to compare against
        return d
    d.missing = [c for c in expected if c not in actual and c not in allow_missing]
    d.unexpected = [c for c in actual if c not in expected]
    for m in d.missing:
        close = difflib.get_close_matches(m, d.unexpected, n=1, cutoff=0.6)
        if close:
            d.renames[m] = close[0]
    return d


def log(con, batch_id: str, d: Drift) -> None:
    con.execute(DDL)
    rows = [(batch_id, d.source, "missing", c, d.renames.get(c), True) for c in d.missing]
    rows += [(batch_id, d.source, "unexpected", c, None, False) for c in d.unexpected]
    for r in rows:
        con.execute("INSERT INTO raw._schema_drift VALUES (now(),?,?,?,?,?,?)", list(r))


def enforce(d: Drift) -> None:
    if d.blocking:
        hint = "; ".join(f"{m} -> {n}?" for m, n in d.renames.items())
        raise SchemaDriftError(
            f"{d.source}: registered columns missing {d.missing}" + (f" (likely renames: {hint})" if hint else "")
        )
