"""Human review: the queue of undecided pairs, and the decisions people made, re-applied on every run.

    data/review/identity_decisions.csv   node_a, node_b, decision, reviewer, decided_ts, evidence_key, note
        decision: match | no_match | unresolved
    gold_stage.identity_review_queue           open pairs, both records side by side, with the Splink and LLM reasons

A decision is keyed by the pair AND a hash of both records' matching values (evidence_key). The same pair
is never asked twice; but if either record changes (a new phone, a corrected DOB), the old decision no
longer applies and the pair can come back: "it resurfaces only if new evidence arrives".
"""
from __future__ import annotations

import csv
from pathlib import Path

from trustissues.common import config

DECISIONS_CSV = config.REPO_ROOT / "data" / "review" / "identity_decisions.csv"
FIELDS = ["node_a", "node_b", "decision", "reviewer", "decided_ts", "evidence_key", "note"]

EVIDENCE_SQL = """md5(concat_ws('|', {n}.first_name, {n}.middle_initials, {n}.last_name, {n}.dob::VARCHAR,
    array_to_string(list_sort({n}.phones), ','), {n}.phone_masked_prefix, {n}.phone_masked_suffix, {n}.postal,
    {n}.employer, {n}.national_id_hash))"""


def evidence_key_sql(a: str = "a", b: str = "b") -> str:
    return f"md5({EVIDENCE_SQL.format(n=a)} || {EVIDENCE_SQL.format(n=b)})"


def load_decisions(con, path: Path = DECISIONS_CSV) -> int:
    """identity.human_decisions: decisions whose evidence still matches the current records."""
    con.execute("""CREATE OR REPLACE TABLE identity.human_decisions (node_a VARCHAR, node_b VARCHAR, decision VARCHAR,
        reviewer VARCHAR, decided_ts VARCHAR, evidence_key VARCHAR, note VARCHAR, still_valid BOOLEAN)""")
    if not path.exists():
        return 0
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("node_a") and r.get("node_b")]
    bad = {r["decision"] for r in rows} - {"match", "no_match", "unresolved"}
    if bad:
        raise ValueError(f"{path}: unknown decision values {bad}")
    if not rows:
        return 0
    con.executemany("INSERT INTO identity.human_decisions VALUES (?,?,?,?,?,?,?, NULL)",
                    [(min(r["node_a"], r["node_b"]), max(r["node_a"], r["node_b"]), r["decision"], r.get("reviewer"),
                      r.get("decided_ts"), r.get("evidence_key"), r.get("note")) for r in rows])
    # the latest decision per pair wins; a decision without evidence_key applies whatever the records say
    con.execute("""DELETE FROM identity.human_decisions h WHERE EXISTS (SELECT 1 FROM identity.human_decisions x
        WHERE x.node_a = h.node_a AND x.node_b = h.node_b AND x.decided_ts > h.decided_ts)""")
    con.execute(f"""UPDATE identity.human_decisions h SET still_valid =
        (h.evidence_key IS NULL OR h.evidence_key = '' OR h.evidence_key = (SELECT {evidence_key_sql()}
            FROM identity.nodes a, identity.nodes b WHERE a.node_id = h.node_a AND b.node_id = h.node_b))""")
    return len(rows)


def build_queue(con) -> dict:
    """gold_stage.identity_review_queue from identity.review_candidates (written by cluster.py)."""
    from .second_opinion import SERIAL_SQL
    con.execute(f"""CREATE OR REPLACE TABLE gold_stage.identity_review_queue AS
        SELECT md5(c.node_a || '|' || c.node_b) AS pair_id, c.node_a, c.node_b, c.reason,
               a.source_system AS source_system_a, b.source_system AS source_system_b,
               ({SERIAL_SQL.replace('n.', 'a.')}) AS record_a, ({SERIAL_SQL.replace('n.', 'b.')}) AS record_b,
               s.match_probability AS splink_probability,
               {_splink_explanation()} AS splink_explanation,
               v.match AS llm_match, v.probability AS llm_probability, v.reason AS llm_reason, v.status AS llm_status,
               c.detail, {evidence_key_sql()} AS evidence_key, 'open' AS status
        FROM identity.review_candidates c
        JOIN identity.nodes a ON a.node_id = c.node_a JOIN identity.nodes b ON b.node_id = c.node_b
        LEFT JOIN identity.splink_pairs s ON s.node_a = c.node_a AND s.node_b = c.node_b
        LEFT JOIN identity.llm_verdicts v ON v.node_a = c.node_a AND v.node_b = c.node_b
        WHERE NOT EXISTS (SELECT 1 FROM identity.human_decisions h WHERE h.node_a = c.node_a AND h.node_b = c.node_b AND h.still_valid)
        QUALIFY row_number() OVER (PARTITION BY c.node_a, c.node_b ORDER BY c.reason) = 1""")
    return dict(con.execute("SELECT reason, count(*) FROM gold_stage.identity_review_queue GROUP BY 1 ORDER BY 1").fetchall())


def _splink_explanation() -> str:
    """'first_name: initial matches | last_name: exact | dob: day/month swapped ...' from the stored gamma columns.

    Splink numbers the non-null levels of a comparison from the top: k-1 for the first, down to 0 for 'different'.
    """
    from .splink_model import comparison_specs
    parts = []
    for name, all_levels in comparison_specs():
        levels = [lv for lv in all_levels if not lv.get("is_null_level")]
        case = " ".join(f"WHEN {len(levels) - 1 - i} THEN '{lv['label_for_charts']}'" for i, lv in enumerate(levels))
        parts.append(f"'{name}: ' || CASE s.gamma_{name} {case} ELSE 'missing' END")
    return "CASE WHEN s.node_a IS NULL THEN NULL ELSE " + " || ' | ' || ".join(parts) + " END"


def ensure_decisions_file(path: Path = DECISIONS_CSV) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(FIELDS)
