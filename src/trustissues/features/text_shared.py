"""Share labelled text features through git without sharing customer text.

gold.text_features rows that were labelled by hand (e.g. the 20,000 done by Luna agents, `model = 'manual:luna-6'`) live
only in the warehouse that imported them. This script writes them to a committed file that holds no note or call text,
and rebuilds the same rows on any machine that has built the warehouse from the same release:

    python -m trustissues.features.text_shared export              # gold.text_features -> data/review/text_features/labels_*.jsonl
    python -m trustissues.features.text_shared import [--dry-run]  # the committed file -> gold.text_features

`make nightly` runs the import as stage `shared_text_features` (before the queue), so a teammate's build picks the
labels up with no extra step. `scripts/text_features_shared.py` is the same command.

One line per text: its `source_id`, the label fields in the same keys the extraction model answers in
(`features/text_extract.py` SYSTEM), the model and extraction time, and for the evidence sentence only its position
`[start, end]` in the scrubbed text. `text_sha256` is a hash of the source ID and the scrubbed text it was labelled on.

Import reads each text from this machine's gold.customer_timeline, scrubs it exactly as the extraction does, and checks
the hash. If it matches, the evidence is cut back out at the stored position; if not (the text differs on this build),
the labels are kept and the evidence is dropped, never guessed. Every row then goes through the production checker
(`text_extract.check`: allowed values, verbatim evidence, derived fields) and writer, so it is identical to a row
extracted on this machine. The person and timestamp come from this machine's timeline, so a rebuilt identity is
followed. No language model is called anywhere here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features import text_extract as tx

OUT_DIR = config.REPO_ROOT / "data" / "review" / "text_features"
# label keys, as the extraction model answers them (text_extract.SYSTEM), and the gold column each one comes from
RAW_FROM_COLUMN = {
    "hardship": "hardship_level", "hardship_reason": "hardship_reason", "stated_delay_reason": "stated_delay_reason",
    "ptp_mentioned": "ptp_mentioned", "ptp_amount": "ptp_amount", "ptp_date": "ptp_date",
    "ptp_intent": "ptp_intent_score", "dispute": "dispute_mentioned", "dispute_type": "dispute_type",
    "cease_request": "cease_request", "vulnerability": "vulnerability", "vulnerability_type": "vulnerability_type",
    "next_step": "next_step", "sentiment": "sentiment", "confidence": "confidence",
}
KEYS = ["source_id", *RAW_FROM_COLUMN, "evidence_span", "text_sha256", "model", "extracted_at"]


def text_hash(source_id: str, text: str) -> str:
    """Salted by the source ID, so two equal texts do not share a hash."""
    return hashlib.sha256(f"{source_id}\n{text}".encode("utf-8")).hexdigest()


def scrubbed_texts(con, source_ids: list[str]) -> dict[str, tx.TextItem]:
    """source_id -> the text as text_extract.load_items would hand it to the model (same filter, same scrubbing)."""
    from trustissues.factory.curate.text_controls import scrub

    do_scrub = config.settings().get("text_controls", {}).get("scrub_pii", True)
    con.execute("CREATE OR REPLACE TEMP TABLE _shared_ids (source_id VARCHAR)")
    con.executemany("INSERT INTO _shared_ids VALUES (?)", [[s] for s in source_ids])
    rows = con.execute("""
        SELECT t.source_id, t.event_type, t.golden_customer_id, t.event_ts, t.text
        FROM gold.customer_timeline t JOIN _shared_ids USING (source_id)
        WHERE t.event_type IN ('note', 'transcript') AND t.text IS NOT NULL""").fetchall()
    return {str(s): tx.TextItem(str(s), e, g, ts, scrub(x).text if do_scrub else x) for s, e, g, ts, x in rows}


def to_line(row: dict, item: tx.TextItem) -> dict:
    """One gold.text_features row -> one shareable line (no text: the evidence becomes its position)."""
    ev = row.get("evidence_sentence")
    start = item.text.find(ev) if ev else -1
    if ev and start < 0:
        raise ValueError(f"{row['source_id']}: evidence is not in the scrubbed text, so it cannot be shared by position")
    line = {"source_id": row["source_id"], **{k: row[c] for k, c in RAW_FROM_COLUMN.items()},
            "evidence_span": [start, start + len(ev)] if ev else None,
            "text_sha256": text_hash(item.source_id, item.text), "model": row["model"],
            "extracted_at": row["extracted_at"].isoformat(timespec="seconds") if row.get("extracted_at") else None}
    return {k: line[k] for k in KEYS}


def from_line(line: dict, item: tx.TextItem) -> tuple[dict, bool]:
    """One shared line + this machine's text -> (a gold.text_features row via the production checker, text matched)."""
    matched = line["text_sha256"] == text_hash(item.source_id, item.text)
    raw = {k: line[k] for k in RAW_FROM_COLUMN}
    span = line.get("evidence_span")
    raw["evidence"] = item.text[span[0]:span[1]] if matched and span else None
    row = tx.check(raw, item, line["model"])
    if line.get("extracted_at"):
        row["extracted_at"] = datetime.fromisoformat(line["extracted_at"])
    return row, matched


def export(con, model: str, path: Path) -> int:
    cur = con.execute("SELECT * FROM gold.text_features WHERE model = ? AND prompt_version = ? ORDER BY source_id",
                      [model, tx.PROMPT_VERSION])
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not rows:
        raise SystemExit(f"no gold.text_features rows with model = {model!r} and prompt_version = {tx.PROMPT_VERSION!r}")
    items = scrubbed_texts(con, [r["source_id"] for r in rows])
    missing = [r["source_id"] for r in rows if r["source_id"] not in items]
    if missing:
        raise SystemExit(f"{len(missing):,} rows have no text in gold.customer_timeline (e.g. {missing[:3]})")
    lines = [to_line(r, items[r["source_id"]]) for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    return len(lines)


def read_lines(files: list[Path]) -> list[dict]:
    lines, seen = [], set()
    for f in files:
        for n, raw in enumerate(f.open(encoding="utf-8"), 1):
            x = json.loads(raw)
            if sorted(x) != sorted(KEYS):
                raise SystemExit(f"{f.name}:{n}: keys {sorted(x)} are not {sorted(KEYS)}")
            if x["source_id"] in seen:
                raise SystemExit(f"{f.name}:{n}: {x['source_id']} appears twice")
            seen.add(x["source_id"])
            lines.append(x)
    return lines


def _write(con, rows: list[dict]) -> None:
    """text_extract.write_rows in one statement each (same delete-then-insert; its row-by-row insert took 160 s here)."""
    import pandas as pd

    tx.ensure_table(con)
    if not rows:
        return
    df = pd.DataFrame([[r.get(c) for c in tx.COLUMNS] for r in rows], columns=tx.COLUMNS)
    con.register("_shared_rows", df)
    con.execute("BEGIN")
    con.execute("DELETE FROM gold.text_features WHERE prompt_version = ? AND source_id IN (SELECT source_id FROM _shared_rows)",
                [tx.PROMPT_VERSION])
    con.execute(f"INSERT INTO gold.text_features ({', '.join(tx.COLUMNS)}) SELECT {', '.join(tx.COLUMNS)} FROM _shared_rows")
    con.execute("COMMIT")
    con.unregister("_shared_rows")


def import_lines(con, lines: list[dict], write: bool = True) -> dict[str, int]:
    """Rebuild the rows on this warehouse; returns counts. Writes only with write=True."""
    items = scrubbed_texts(con, [x["source_id"] for x in lines])
    rows, matched = [], 0
    for x in lines:
        item = items.get(x["source_id"])
        if item is None:  # this build has no such text (another release): nothing to attach the labels to
            continue
        row, ok = from_line(x, item)
        rows.append(row)
        matched += ok
    if write:
        _write(con, rows)
    return {"shared": len(lines), "found": len(rows), "identical": matched, "different": len(rows) - matched,
            "absent": len(lines) - len(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--model", default="manual:luna-6", help="the gold.text_features.model to share")
    i = sub.add_parser("import")
    i.add_argument("--dry-run", action="store_true", help="check and report, write nothing")
    for p in (e, i):
        p.add_argument("--dir", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)

    if a.cmd == "export":
        con = connect(read_only=True)
        path = a.dir / f"labels_{a.model.replace(':', '_')}.jsonl"
        print(f"{export(con, a.model, path):,} rows -> {path}")
        return 0

    files = sorted(a.dir.glob("labels_*.jsonl"))
    if not files:
        print(f"nothing to import: no labels_*.jsonl in {a.dir}")
        return 0
    lines = read_lines(files)
    con = connect(read_only=a.dry_run)
    s = import_lines(con, lines, write=not a.dry_run)
    print(f"{s['shared']:,} shared labels: {s['found']:,} texts found, {s['identical']:,} identical (evidence restored), "
          f"{s['different']:,} different (labels kept, evidence dropped), {s['absent']:,} not in this warehouse")
    if a.dry_run:
        return 0
    n = con.execute("SELECT count(*) FROM gold.text_features WHERE source_id IN (SELECT source_id FROM _shared_ids)").fetchone()[0]
    print(f"gold.text_features now holds {n:,} of them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
