"""Per-file fingerprint, row count, columns and checksum in raw._load_manifest.

The manifest is what makes loading incremental: a file whose size and mtime (and, if those
changed, sha256) match the manifest is not read again.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS raw._load_manifest (
    source VARCHAR, file VARCHAR, bytes BIGINT, mtime DOUBLE, sha256 VARCHAR,
    row_count BIGINT, columns VARCHAR, batch_id VARCHAR, loaded_at TIMESTAMP
)"""


@dataclass
class FileInfo:
    path: Path
    rel: str
    bytes: int
    mtime: float
    sha256: str | None = None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stat(path: Path, root: Path) -> FileInfo:
    st = path.stat()
    return FileInfo(path, path.relative_to(root).as_posix(), st.st_size, st.st_mtime)


def changed_files(con, source: str, files: list[FileInfo]) -> list[FileInfo]:
    """Files that are new or whose content differs from what the manifest recorded."""
    con.execute(DDL)
    known = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT file, bytes, mtime, sha256 FROM raw._load_manifest WHERE source = ?", [source]
        ).fetchall()
    }
    out = []
    for f in files:
        prev = known.get(f.rel)
        if prev is None:
            f.sha256 = sha256_of(f.path)
            out.append(f)
        elif (prev[0], prev[1]) != (f.bytes, f.mtime):
            f.sha256 = sha256_of(f.path)
            if f.sha256 != prev[2]:
                out.append(f)
    return out


def record(con, source: str, f: FileInfo, rows: int, columns: list[str], batch_id: str) -> None:
    con.execute("DELETE FROM raw._load_manifest WHERE source = ? AND file = ?", [source, f.rel])
    con.execute(
        "INSERT INTO raw._load_manifest VALUES (?,?,?,?,?,?,?,?, now())",
        [source, f.rel, f.bytes, f.mtime, f.sha256, rows, ",".join(columns), batch_id],
    )
