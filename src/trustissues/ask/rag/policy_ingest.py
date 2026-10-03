"""Chunk the reference documents by section and attach the metadata retrieval filters on.

One chunk per section; a long section is split into sub-chunks that never cross the section
boundary and overlap by one sentence. Every chunk keeps its parent section text so retrieval can
expand a hit to the whole section. Document metadata comes from raw.reference_documents.

Section shapes found in the release: `## §4.2 Title` headings, FAQ `**Q1. ...**` blocks, a
programme table (one section per row, keyed by programme ID) and headingless bodies (one section).
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field

from trustissues.common import config
from trustissues.common.db import connect

MAX_WORDS = 180  # sub-chunk size; open in the design (D6), tune against the benchmark
HEADING = re.compile(r"^##\s+(\S+)\s*(.*)$")
FAQ_Q = re.compile(r"^\*\*(Q\d+)\.\s*(.*?)\*\*\s*$")
ROW = re.compile(r"^\|\s*([^|]+?)\s*\|")
SENT = re.compile(r"(?<=[.!?])\s+")
WHOLE = "(whole document)"


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    section_id: str
    section_title: str
    text: str  # what is embedded and searched
    section_text: str  # parent section, returned on expansion
    meta: dict = field(default_factory=dict)


def _sections(body: str) -> list[tuple[str, str, str]]:
    """Return (section_id, title, text) for a document body (title line and banner already removed)."""
    lines = body.splitlines()
    out: list[tuple[str, str, list[str]]] = []
    for ln in lines:
        if m := HEADING.match(ln):
            out.append((m.group(1), m.group(2), []))
        elif m := FAQ_Q.match(ln):
            out.append((m.group(1), m.group(2), [ln]))
        elif out:
            out[-1][2].append(ln)
    if out:
        return [(i, t, "\n".join(x).strip()) for i, t, x in out if "\n".join(x).strip()]

    rows = [ln for ln in lines if ln.startswith("|")]
    if len(rows) > 2:  # header, separator, then one section per data row
        head = rows[0]
        return [(ROW.match(r).group(1), "", f"{head}\n{rows[1]}\n{r}") for r in rows[2:]]
    text = body.strip()
    return [(WHOLE, "", text)] if text else []


def _split(text: str, max_words: int = MAX_WORDS) -> list[str]:
    """Split on sentence boundaries with a one-sentence overlap; table rows are never cut."""
    if len(text.split()) <= max_words:
        return [text]
    sents = [s for s in SENT.split(text.replace("\n", " \n ")) if s.strip()]
    parts, cur = [], []
    for s in sents:
        if cur and len(" ".join(cur + [s]).split()) > max_words:
            parts.append(" ".join(cur).replace(" \n ", "\n").strip())
            cur = [cur[-1]]  # overlap
        cur.append(s)
    if cur:
        parts.append(" ".join(cur).replace(" \n ", "\n").strip())
    return parts


def chunk_document(doc: dict, markdown: str) -> list[Chunk]:
    body = "\n".join(
        ln for ln in markdown.splitlines() if not ln.startswith(">") and not ln.startswith("# ")
    )
    meta = {k: doc[k] for k in (
        "doc_id", "doc_type", "title", "language", "version", "effective_date", "superseded_by",
        "owner", "confidentiality", "applies_to_products", "applies_to_provinces",
    )}
    meta["applies_to_products"] = json.loads(meta["applies_to_products"] or "[]")
    meta["applies_to_provinces"] = json.loads(meta["applies_to_provinces"] or "[]")
    chunks: list[Chunk] = []
    for sid, title, text in _sections(body):
        parts = _split(text)
        for i, part in enumerate(parts):
            label = f"{doc['title']} {sid} {title}".strip() if sid != WHOLE else doc["title"]
            chunks.append(Chunk(
                chunk_id=f"{doc['doc_id']}|v{doc['version']}|{sid}|{i}",
                doc_id=doc["doc_id"], section_id=sid, section_title=title,
                text=f"{label}\n{part}",  # heading prefixed so the section's own name is searchable
                section_text=text, meta=meta,
            ))
    return chunks


def build() -> list[Chunk]:
    root = config.maple_data_dir()
    con = connect(read_only=True)
    cur = con.execute("SELECT * EXCLUDE (_ingested_at) FROM raw.reference_documents ORDER BY doc_id")
    cols = [d[0] for d in cur.description]
    docs = [dict(zip(cols, r)) for r in cur.fetchall()]
    con.close()
    chunks: list[Chunk] = []
    for d in docs:
        md = (root / "files" / d["file_path"]).read_text(encoding="utf-8")
        chunks += chunk_document(d, md)
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser(description="Chunk policy documents into warehouse/rag/policy_chunks.jsonl")
    ap.parse_args()
    chunks = build()
    out = config.warehouse_path().parent / "rag" / "policy_chunks.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    docs = {c.doc_id for c in chunks}
    print(f"{len(chunks)} chunks from {len(docs)} documents -> {out}")


if __name__ == "__main__":
    main()
