"""Text features: the checks around the model (allowed values, verbatim evidence, derived fields, batching,
resume, I3 shape) and the evaluation maths. A scripted stand-in replaces the model."""
import json
from datetime import datetime

import duckdb
import pandas as pd
import pytest

from trustissues.common.llm import LLM, ResponseCache
from trustissues.features import text_extract as tx
from trustissues.features.text_eval import accuracy, binary, evaluate

NOTE = tx.TextItem("N-1", "note", "G-1", datetime(2026, 9, 1, 10), "spoke w/ cust; hours cut at work; can pay $95 Oct 14")
CALL = tx.TextItem("77", "transcript", "G-2", datetime(2026, 9, 2, 11),
                   "agent: Hello.\ncustomer: The late fee is wrong, I paid on time.\ncustomer: Please stop calling me.")


def raw(**kw):
    base = {"hardship": "clear", "hardship_reason": "reduced_income", "stated_delay_reason": "payroll_timing",
            "ptp_mentioned": True, "ptp_amount": "$95", "ptp_date": "Oct 14", "ptp_intent": 0.85, "dispute": False,
            "dispute_type": None, "cease_request": False, "vulnerability": False, "vulnerability_type": "mental_health",
            "next_step": "send_payment_link", "sentiment": "cooperative", "evidence": "hours cut at work",
            "confidence": 0.9}
    return {**base, **kw}


# ---- checks on one reply -----------------------------------------------------------------------
def test_a_good_reply_becomes_a_full_row_with_the_i3_columns():
    r = tx.check(raw(), NOTE, "m")
    assert set(tx.I3_COLUMNS) <= set(r)
    assert (r["hardship_signal"], r["ptp_amount"], r["ptp_intent_strength"], r["evidence_sentence"]) == \
        (True, 95.0, "strong", "hours cut at work")
    assert r["stated_delay_reason"] == "reduced_income"  # derived from the hardship reason, not the model's guess
    assert r["vulnerability_type"] is None  # no vulnerability, so no type


def test_values_outside_the_allowed_lists_become_null():
    r = tx.check(raw(hardship="severe", next_step="send flowers", sentiment="ANGRY", ptp_intent=3), NOTE, "m")
    assert r["hardship_level"] is None and r["hardship_signal"] is None
    assert r["next_step"] is None and r["sentiment"] is None and r["ptp_intent_score"] is None


def test_evidence_not_in_the_text_is_dropped_and_confidence_lowered():
    r = tx.check(raw(evidence="customer lost their job", confidence=0.95), NOTE, "m")
    assert r["evidence_sentence"] is None and r["confidence"] == 0.5


def test_a_dispute_is_the_stated_reason_and_keeps_its_type():
    r = tx.check(raw(hardship="none", dispute=True, dispute_type="late_fee", stated_delay_reason="forgot",
                     evidence="customer: The late fee is wrong, I paid on time."), CALL, "m")
    assert (r["stated_delay_reason"], r["dispute_type"], r["hardship_reason"]) == ("dispute", "late_fee", None)
    assert r["evidence_sentence"].startswith("customer:")


@pytest.mark.parametrize("reason, expected", [("job_loss", "reduced_income"), ("ei_waiting", "reduced_income"),
                                              ("illness", "other"), ("rate_reset", "other"), ("separation", "other")])
def test_hardship_reason_sets_the_stated_reason(reason, expected):
    assert tx.check(raw(hardship_reason=reason), NOTE, "m")["stated_delay_reason"] == expected


def test_no_promise_means_no_amount_or_intent():
    r = tx.check(raw(ptp_mentioned=False), NOTE, "m")
    assert (r["ptp_amount"], r["ptp_intent_score"], r["ptp_intent_strength"]) == (None, None, "none")


@pytest.mark.parametrize("score, band", [(0.85, "strong"), (0.7, "strong"), (0.6, "moderate"), (0.45, "weak")])
def test_intent_bands(score, band):
    assert tx.intent_band(score, True) == band


# ---- batching, model call, table ---------------------------------------------------------------
def test_notes_and_transcripts_are_batched_separately():
    items = [NOTE] * 45 + [CALL] * 9
    sizes = [(b[0].source_type, len(b)) for b in tx.batches(items, note_batch=20, transcript_batch=4)]
    assert sizes == [("note", 20), ("note", 20), ("note", 5), ("transcript", 4), ("transcript", 4), ("transcript", 1)]


class Scripted:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def complete(self, **kw):
        self.calls.append(kw)
        return json.dumps(self.reply)


def test_batch_call_matches_replies_by_id_and_skips_missing_ones(tmp_path):
    be = Scripted({"items": [dict(raw(), id="77", dispute=True, evidence=None)]})  # N-1 left out by the model
    llm = LLM(be, cache=ResponseCache(tmp_path / "c.sqlite"), models={"answer": "m", "bulk": "m-bulk"},
              max_retries=0, sleep=lambda s: None)
    rows = tx.extract_batch([NOTE, CALL], llm)
    assert [r["source_id"] for r in rows] == ["77"] and rows[0]["model"] == "m-bulk"
    assert "### id: N-1 (agent note, 2026-09-01)" in be.calls[0]["user"]


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA gold")
    c.execute("CREATE SCHEMA curated")
    c.execute("""CREATE TABLE gold.customer_timeline (golden_customer_id VARCHAR, event_ts TIMESTAMP, event_type VARCHAR,
                 source_table VARCHAR, source_id VARCHAR, account_id VARCHAR, agent_id VARCHAR, channel VARCHAR,
                 text VARCHAR, voice_sample_id VARCHAR)""")
    c.execute("""INSERT INTO gold.customer_timeline VALUES
        ('G-1', '2026-09-01 10:00', 'note', NULL, 'N-1', NULL, NULL, NULL, 'call me at 416-555-0199 re hours cut', NULL),
        ('G-1', '2026-09-02 10:00', 'note', NULL, 'N-2', NULL, NULL, NULL, NULL, NULL),
        ('G-1', '2026-09-03 10:00', 'contact', NULL, 'C-1', NULL, NULL, NULL, NULL, NULL),
        ('G-2', '2026-09-04 10:00', 'transcript', NULL, '77', NULL, NULL, NULL, 'customer: hi', NULL)""")
    return c


def test_table_gets_the_extra_columns_and_rerun_skips_done_texts(con):
    items = tx.load_items(con, "all", scrub_text=True)
    assert [i.source_id for i in items] == ["77", "N-1"]  # newest first; N-2 has no text, C-1 is not text
    assert "[PHONE]" in items[1].text and "416-555-0199" not in items[1].text
    tx.write_rows(con, [tx.check(raw(evidence=None), items[1], "m")])
    tx.write_rows(con, [tx.check(raw(evidence=None), items[1], "m")])  # written twice: still one row
    assert con.execute("SELECT count(*) FROM gold.text_features").fetchone()[0] == 1
    assert [i.source_id for i in tx.load_items(con, "all")] == ["77"]
    cols = {r[0] for r in con.execute("DESCRIBE gold.text_features").fetchall()}
    assert set(tx.COLUMNS) <= cols


# ---- evaluation maths --------------------------------------------------------------------------
def test_binary_and_accuracy_scores():
    s = binary("x", pd.Series([True, True, False, False]), pd.Series([True, False, True, False]))
    assert s.value == pytest.approx(0.5) and "precision 0.50, recall 0.50" in s.detail
    a = accuracy("r", pd.Series(["forgot", None, "travel"]), pd.Series(["forgot", None, "other"]))
    assert a.value == pytest.approx(2 / 3)


def test_evaluate_reports_coverage_and_the_gate():
    labels = pd.DataFrame({
        "source_id": ["N-1", "N-2"], "source_type": ["note", "note"], "label_hardship": ["clear", "none"],
        "label_hardship_reason": ["job_loss", None], "label_stated_delay_reason": ["reduced_income", "forgot"],
        "label_ptp_mentioned": [True, False], "label_ptp_intent_strength": [0.85, None], "label_dispute": [False, False],
        "label_cease_request": [False, False], "label_vulnerability": [False, False],
        "label_next_step": ["call_back", "call_back"], "label_sentiment": ["neutral", "neutral"]})
    feats = pd.DataFrame([tx.check(raw(hardship_reason="job_loss", next_step="call_back", sentiment="neutral",
                                       evidence=None), NOTE, "m")])
    scores, meta, _ = evaluate(labels, feats)
    assert meta["coverage"]["note"] == (1, 2)  # N-2 had no features
    assert meta["gate"]["stated_delay_reason accuracy"] == 1.0
