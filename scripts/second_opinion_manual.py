"""The identity second opinion done by labellers (people or agents) instead of an API call.

factory/identity/second_opinion.py asks an LLM whether each grey-zone pair (Splink 0.80-0.95) is the same person. It is
on hold until the team agrees which provider may receive these pairs (merge.md §3). This script does the same job by
hand: export the pairs to files, have labellers fill in a verdict per pair, then import the verdicts into
identity.llm_verdicts exactly as the LLM step would have written them. The identity build then uses them unchanged:
a grey pair links only if its verdict says match; anything else goes to human review and the records stay separate.

    python scripts/second_opinion_manual.py export --shards 3            # data/review/second_opinion/shard_<k>.jsonl
    python scripts/second_opinion_manual.py import --labeller luna        # reads verdicts_*.jsonl, writes the verdicts
    python -m trustissues.factory.identity.build --from relationships     # then rebuild identity from the verdicts
    python -m trustissues.factory.pipeline --from gold                    # and gold, views, publish, identity report

Export: one JSON line per pair {pair_key, node_a, node_b, record_a, record_b, splink_probability, overlap}. Pairs are
dealt round-robin (each shard gets the same mix of easy and hard pairs). With --overlap N, each shard also gets N pairs
of the next shard, marked overlap = true, so two labellers judge them blind and their agreement can be measured.
The shard files hold unmasked names, dates of birth and phone numbers: everything in data/review/second_opinion/ is
gitignored except verdicts_*.jsonl, which hold no record text (pair hash, verdict, generic reason) and are committed so
every machine imports the same judgements.

Import: every verdicts_*.jsonl line is {pair_key, match, probability, deciding_fields, reason}. Nothing is written
unless every line is valid (known pair_key, match a boolean, probability 0-1 and on the same side of 0.5 as match,
deciding_fields from FIELDS, a reason). A verdict for a pair this build does not call grey is skipped and counted
(Splink is retrained each build and can move a pair out of the grey zone). A pair judged twice that the labellers disagree on is stored as no match
(it goes to human review), and the agreement rate is printed. Pairs nobody judged stay 'unavailable' (human review).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.factory.identity.second_opinion import SERIAL_SQL

OUT_DIR = config.REPO_ROOT / "data" / "review" / "second_opinion"
FIELDS = {"source", "name", "dob", "phone", "postal", "employer"}


def grey_pairs(con) -> list[dict]:
    """The grey-zone pairs as second_opinion.build sees them, with the same pair_key."""
    version = config.settings()["identity"]["llm_prompt_version"]
    rows = con.execute(f"""
        SELECT p.node_a, p.node_b, ({SERIAL_SQL.replace('n.', 'a.')}), ({SERIAL_SQL.replace('n.', 'b.')}), p.match_probability
        FROM identity.splink_pairs p JOIN identity.nodes a ON a.node_id = p.node_a JOIN identity.nodes b ON b.node_id = p.node_b
        WHERE p.route = 'grey' ORDER BY p.match_probability DESC, p.node_a, p.node_b""").fetchall()
    return [{"pair_key": hashlib.md5(f"{version}|{ra} [SEP] {rb}".encode()).hexdigest(), "node_a": a, "node_b": b,
             "record_a": ra, "record_b": rb, "splink_probability": round(p, 4)} for a, b, ra, rb, p in rows]


def export(pairs: list[dict], shards: int, overlap: int, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dealt = [pairs[k::shards] for k in range(shards)]
    paths = []
    for k in range(shards):
        extra = dealt[(k + 1) % shards][:overlap] if shards > 1 else []
        path = out_dir / f"shard_{k + 1}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for p, ov in [(p, False) for p in dealt[k]] + [(p, True) for p in extra]:
                f.write(json.dumps({**p, "overlap": ov}, ensure_ascii=False) + "\n")
        paths.append(path)
    return paths


def validate(line: dict, known: set[str]) -> str | None:
    """Why a verdict line is invalid, or None."""
    if line.get("pair_key") not in known:
        return "unknown pair_key"
    if not isinstance(line.get("match"), bool):
        return "match must be true or false"
    p = line.get("probability")
    if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 <= p <= 1:
        return "probability must be a number from 0 to 1"
    if (p >= 0.5) != line["match"]:
        return "probability and match disagree (match needs >= 0.5, no match < 0.5)"
    f = line.get("deciding_fields")
    if not isinstance(f, list) or not f or not set(f) <= FIELDS:
        return f"deciding_fields must be a non-empty list from {sorted(FIELDS)}"
    if not isinstance(line.get("reason"), str) or not line["reason"].strip():
        return "reason is required"
    return None


def combine(lines: list[dict]) -> tuple[dict[str, dict], dict]:
    """One verdict per pair. Two labellers who disagree -> no match (human review)."""
    by_key: dict[str, list[dict]] = {}
    for line in lines:
        by_key.setdefault(line["pair_key"], []).append(line)
    out, twice, agree = {}, 0, 0
    for key, vs in by_key.items():
        if len(vs) == 1:
            out[key] = vs[0]
            continue
        twice += 1
        if len({v["match"] for v in vs}) == 1:
            agree += 1
            out[key] = {**vs[0], "probability": sum(v["probability"] for v in vs) / len(vs)}
        else:
            out[key] = {"pair_key": key, "match": False, "probability": 0.0,
                        "deciding_fields": sorted({f for v in vs for f in v["deciding_fields"]}),
                        "reason": "labellers disagreed: " + " | ".join(v["reason"] for v in vs)}
    return out, {"judged_twice": twice, "agreed": agree}


def read_verdicts(files: list[Path], known: set[str]) -> tuple[list[dict], list[str], int]:
    """(valid lines, errors, skipped). A verdict for a pair this build does not call grey is skipped, not an error:
    Splink is retrained on every build, and a pair near 0.80 or 0.95 can move out of the grey zone (2 of 3,342 on the
    4 Oct rebuild), so verdicts shared from another machine or an earlier build must still import. A malformed line is
    an error, and any error means nothing is written."""
    lines, errors, skipped = [], [], 0
    for path in files:
        for n, raw in enumerate(path.open(encoding="utf-8"), 1):
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                errors.append(f"{path.name}:{n}: not JSON")
                continue
            if isinstance(line, dict) and line.get("pair_key") not in known:
                skipped += 1
                continue
            why = validate(line, known)
            if why:
                errors.append(f"{path.name}:{n}: {why}")
            else:
                lines.append(line)
    return lines, errors, skipped


def write_verdicts(con, pairs: list[dict], verdicts: dict[str, dict], labeller: str) -> int:
    version = config.settings()["identity"]["llm_prompt_version"]
    con.execute("""CREATE TABLE IF NOT EXISTS identity.llm_verdicts (pair_key VARCHAR, node_a VARCHAR, node_b VARCHAR,
        serialised VARCHAR, match BOOLEAN, probability DOUBLE, deciding_fields VARCHAR, reason VARCHAR, status VARCHAR,
        model VARCHAR, prompt_version VARCHAR, asked_ts TIMESTAMP)""")
    rows = [(p["pair_key"], p["node_a"], p["node_b"], f"{p['record_a']} [SEP] {p['record_b']}", v["match"],
             float(v["probability"]), json.dumps(v["deciding_fields"]), v["reason"].strip()[:500], "ok",
             f"manual:{labeller}", version) for p in pairs if (v := verdicts.get(p["pair_key"]))]
    con.execute("BEGIN")
    con.executemany("DELETE FROM identity.llm_verdicts WHERE node_a = ? AND node_b = ?", [(r[1], r[2]) for r in rows])
    con.executemany("INSERT INTO identity.llm_verdicts VALUES (?,?,?,?,?,?,?,?,?,?,?, now())", rows)
    exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gold' "
                         "AND table_name = 'audit_log'").fetchone()[0]
    if exists:  # same audit trail as the LLM step (kind identity_pair), marked as a manual labeller
        con.executemany("""INSERT INTO gold.audit_log (ts, user_role, user_id, kind, input_text, sources, prompt, answer,
            model, prompt_version) VALUES (now(), 'labeller', ?, 'identity_pair', ?, ?, ?, ?, ?, ?)""",
                        [(labeller, r[0], json.dumps([r[1], r[2]]), r[3],
                          json.dumps({"match": r[4], "probability": r[5], "reason": r[7]}), r[9], r[10]) for r in rows])
    con.execute("COMMIT")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--shards", type=int, default=3)
    e.add_argument("--overlap", type=int, default=30, help="pairs of the next shard each labeller also judges")
    i = sub.add_parser("import")
    i.add_argument("--labeller", required=True, help="who judged the pairs, e.g. luna (stored as model manual:<name>)")
    i.add_argument("--dry-run", action="store_true", help="validate and report, write nothing")
    for p in (e, i):
        p.add_argument("--dir", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)

    if a.cmd == "export":
        con = connect(read_only=True)
        pairs = grey_pairs(con)
        con.close()
        for path in export(pairs, a.shards, a.overlap, a.dir):
            print(f"{path}: {sum(1 for _ in path.open(encoding='utf-8')):,} pairs")
        print(f"{len(pairs):,} grey pairs, {a.shards} shards, {a.overlap} overlap pairs each")
        return 0

    con = connect(read_only=a.dry_run)
    pairs = grey_pairs(con)
    files = sorted(a.dir.glob("verdicts_*.jsonl"))
    lines, errors, skipped = read_verdicts(files, {p["pair_key"] for p in pairs})
    if skipped:
        print(f"{skipped:,} verdicts are for pairs that are not grey in this build (Splink retrains each run and can "
              "move a pair across 0.80 / 0.95): skipped")
    if not files or errors:
        print("\n".join(errors[:50]) or f"no verdicts_*.jsonl in {a.dir}", file=sys.stderr)
        print(f"{len(errors):,} invalid lines: nothing written", file=sys.stderr)
        con.close()
        return 1
    verdicts, overlap = combine(lines)
    matches = sum(v["match"] for v in verdicts.values())
    print(f"{len(verdicts):,} of {len(pairs):,} grey pairs judged ({len(pairs) - len(verdicts):,} not judged stay "
          f"with human review); {matches:,} match, {len(verdicts) - matches:,} no match")
    if overlap["judged_twice"]:
        print(f"judged twice: {overlap['judged_twice']:,}, agreed {overlap['agreed']:,} "
              f"({overlap['agreed'] / overlap['judged_twice']:.1%}); disagreements stored as no match")
    if not a.dry_run:
        print(f"wrote {write_verdicts(con, pairs, verdicts, a.labeller):,} verdicts to identity.llm_verdicts")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
