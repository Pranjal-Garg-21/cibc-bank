"""features/text_shared.py: hand-labelled text features shared through git without the customer's text."""
import json
from datetime import datetime

import duckdb
import pytest

from trustissues.common.interfaces import apply_stubs
from trustissues.features import text_extract as tx
from trustissues.features import text_shared as m

NOTE = "Cust lost job last month. PTP $95 Oct 14. CB scheduled."
CALL = "agent: Hello.\ncustomer: I was laid off, can I pay 50 on Friday?"


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    apply_stubs(c)
    tx.ensure_table(c)
    # gold.customer_timeline comes from the interface stubs
    c.execute("""INSERT INTO gold.customer_timeline (golden_customer_id, event_ts, event_type, source_id, text) VALUES
                 ('G-1', TIMESTAMP '2026-09-26 10:00:00', 'note', 'N-1', ?),
                 ('G-2', TIMESTAMP '2026-09-27 11:00:00', 'transcript', 'T-1', ?)""", [NOTE, CALL])
    items = m.scrubbed_texts(c, ["N-1", "T-1"])
    answers = {
        "N-1": {"hardship": "clear", "hardship_reason": "job_loss", "ptp_mentioned": True, "ptp_amount": 95,
                "ptp_date": "Oct 14", "ptp_intent": 0.85, "dispute": False, "next_step": "call_back",
                "sentiment": "cooperative", "evidence": "Cust lost job last month.", "confidence": 0.9},
        "T-1": {"hardship": "clear", "hardship_reason": "job_loss", "ptp_mentioned": True, "ptp_amount": 50,
                "ptp_date": "Friday", "ptp_intent": 0.5, "sentiment": "distressed", "evidence": None, "confidence": 0.7},
    }
    tx.write_rows(c, [tx.check(answers[s], items[s], "manual:test") for s in ("N-1", "T-1")])
    return c


def _gold(con):
    cur = con.execute("SELECT * FROM gold.text_features ORDER BY source_id")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def test_the_shared_file_holds_no_text(con, tmp_path):
    path = tmp_path / "labels.jsonl"
    assert m.export(con, "manual:test", path) == 2
    raw = path.read_text(encoding="utf-8")
    assert "lost job" not in raw and "laid off" not in raw  # neither the note nor the evidence sentence
    line = json.loads(raw.splitlines()[0])
    assert sorted(line) == sorted(m.KEYS) and line["evidence_span"] == [0, len("Cust lost job last month.")]


def test_import_rebuilds_the_same_rows(con, tmp_path):
    before = _gold(con)
    m.export(con, "manual:test", tmp_path / "labels_manual_test.jsonl")
    con.execute("DELETE FROM gold.text_features")
    stats = m.import_lines(con, m.read_lines([tmp_path / "labels_manual_test.jsonl"]))
    assert stats == {"shared": 2, "found": 2, "identical": 2, "different": 0, "absent": 0}
    after = _gold(con)
    for b, a in zip(before, after):
        assert {k: v for k, v in a.items() if k != "extracted_at"} == {k: v for k, v in b.items() if k != "extracted_at"}
        assert a["extracted_at"].replace(microsecond=0) == b["extracted_at"].replace(microsecond=0)
    assert after[0]["evidence_sentence"] == "Cust lost job last month."


def test_a_text_that_differs_keeps_its_labels_but_never_a_guessed_evidence(con, tmp_path):
    m.export(con, "manual:test", tmp_path / "labels.jsonl")
    line = m.read_lines([tmp_path / "labels.jsonl"])[0]
    changed = tx.TextItem("N-1", "note", "G-1", datetime(2026, 9, 26, 10), "Xust lost job last month. PTP $95 Oct 14.")
    row, ok = m.from_line(line, changed)
    assert not ok and row["evidence_sentence"] is None
    assert row["hardship_signal"] is True and row["ptp_amount"] == 95  # labels kept


def test_the_person_comes_from_this_machines_timeline(con, tmp_path):
    m.export(con, "manual:test", tmp_path / "labels.jsonl")
    line = m.read_lines([tmp_path / "labels.jsonl"])[0]
    con.execute("UPDATE gold.customer_timeline SET golden_customer_id = 'G-9' WHERE source_id = 'N-1'")  # identity rebuilt
    row, ok = m.from_line(line, m.scrubbed_texts(con, ["N-1"])["N-1"])
    assert ok and row["golden_customer_id"] == "G-9"


def test_a_file_with_a_duplicate_or_a_wrong_key_is_refused(con, tmp_path):
    path = tmp_path / "labels.jsonl"
    m.export(con, "manual:test", path)
    first = path.read_text(encoding="utf-8").splitlines()[0]
    (tmp_path / "dup.jsonl").write_text(first + "\n" + first + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="appears twice"):
        m.read_lines([tmp_path / "dup.jsonl"])
    bad = json.loads(first)
    bad["evidence"] = "Cust lost job last month."
    (tmp_path / "bad.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="keys"):
        m.read_lines([tmp_path / "bad.jsonl"])
