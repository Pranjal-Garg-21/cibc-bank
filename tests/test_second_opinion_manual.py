"""scripts/second_opinion_manual.py: grey pairs out to labellers, checked verdicts back into identity.llm_verdicts."""
import json
import sys

import duckdb
import pytest

from trustissues.common import config

sys.path.insert(0, str(config.REPO_ROOT / "scripts"))
import second_opinion_manual as m  # noqa: E402


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA identity")
    c.execute("""CREATE TABLE identity.nodes (node_id VARCHAR, source_system VARCHAR, first_name VARCHAR,
        middle_initials VARCHAR, last_name VARCHAR, dob DATE, phones VARCHAR[], phone_masked_prefix VARCHAR,
        phone_masked_suffix VARCHAR, postal VARCHAR, employer VARCHAR)""")
    c.execute("""INSERT INTO identity.nodes VALUES
        ('crm|1','crm','ANNA',NULL,'LEE','1988-03-14',['5195550142'],NULL,NULL,'N9A4J2',NULL),
        ('card|1','card','A',NULL,'LEE','1988-03-14',[],'519','42','N9A4J2',NULL),
        ('crm|2','crm','BO',NULL,'KIM','1970-01-02',['4165550000'],NULL,NULL,'M5V2T6','ACME'),
        ('loan|2','loan','BO',NULL,'KIM','1970-02-01',[],NULL,NULL,'M5V2T6',NULL),
        ('crm|3','crm','CY',NULL,'NG',NULL,[],NULL,NULL,NULL,NULL),
        ('dep|3','deposit','C',NULL,'NG',NULL,[],NULL,NULL,NULL,NULL)""")
    c.execute("CREATE TABLE identity.splink_pairs (node_a VARCHAR, node_b VARCHAR, match_probability DOUBLE, route VARCHAR)")
    c.execute("""INSERT INTO identity.splink_pairs VALUES ('crm|1','card|1',0.93,'grey'), ('crm|2','loan|2',0.85,'grey'),
                 ('crm|3','dep|3',0.81,'grey'), ('crm|1','crm|2',0.99,'link')""")
    return c


def verdict(key, match=True, p=0.9, fields=("name", "dob"), reason="same name and date of birth"):
    return {"pair_key": key, "match": match, "probability": p, "deciding_fields": list(fields), "reason": reason}


def test_only_grey_pairs_are_exported_with_readable_records(con):
    pairs = m.grey_pairs(con)
    assert [(p["node_a"], p["node_b"]) for p in pairs] == [("crm|1", "card|1"), ("crm|2", "loan|2"), ("crm|3", "dep|3")]
    assert pairs[0]["record_b"] == "[SRC] card [NAME] A LEE [DOB] 1988-03-14 [PHONE] 519xxx42 [POSTAL] N9A4J2"
    assert len({p["pair_key"] for p in pairs}) == 3


def test_shards_are_dealt_round_robin_with_blind_overlap(con, tmp_path):
    pairs = m.grey_pairs(con)
    paths = m.export(pairs, 3, 1, tmp_path)
    shards = [[json.loads(x) for x in p.open()] for p in paths]
    assert [[r["pair_key"] for r in s if not r["overlap"]] for s in shards] == [[p["pair_key"]] for p in pairs]
    assert [r["pair_key"] for r in shards[0] if r["overlap"]] == [pairs[1]["pair_key"]]  # next shard's pair


@pytest.mark.parametrize("change,why", [
    ({"pair_key": "nope"}, "unknown"),
    ({"match": "yes"}, "true or false"),
    ({"probability": 1.5}, "0 to 1"),
    ({"probability": 0.3}, "disagree"),
    ({"deciding_fields": ["vibes"]}, "deciding_fields"),
    ({"deciding_fields": []}, "deciding_fields"),
    ({"reason": " "}, "reason"),
])
def test_invalid_verdicts_are_refused(con, change, why):
    key = m.grey_pairs(con)[0]["pair_key"]
    assert why in m.validate({**verdict(key), **change}, {key})
    assert m.validate(verdict(key), {key}) is None


def test_disagreement_becomes_no_match_and_agreement_is_counted():
    out, stats = m.combine([verdict("k1"), verdict("k1", p=0.7), verdict("k2"),
                            verdict("k2", match=False, p=0.2, reason="different first name")])
    assert stats == {"judged_twice": 2, "agreed": 1}
    assert out["k1"]["match"] and out["k1"]["probability"] == pytest.approx(0.8)
    assert not out["k2"]["match"] and out["k2"]["reason"].startswith("labellers disagreed")


def test_verdicts_replace_unavailable_rows_as_the_llm_step_would_write_them(con):
    pairs = m.grey_pairs(con)
    con.execute("""CREATE TABLE identity.llm_verdicts (pair_key VARCHAR, node_a VARCHAR, node_b VARCHAR,
        serialised VARCHAR, match BOOLEAN, probability DOUBLE, deciding_fields VARCHAR, reason VARCHAR, status VARCHAR,
        model VARCHAR, prompt_version VARCHAR, asked_ts TIMESTAMP)""")
    con.executemany("INSERT INTO identity.llm_verdicts VALUES (?,?,?,NULL,NULL,NULL,NULL,'no LLM client','unavailable',"
                    "NULL,'identity_pair_v1',now())", [(p["pair_key"], p["node_a"], p["node_b"]) for p in pairs])
    verdicts = {pairs[0]["pair_key"]: verdict(pairs[0]["pair_key"]),
                pairs[1]["pair_key"]: verdict(pairs[1]["pair_key"], match=False, p=0.1, fields=["dob"])}
    assert m.write_verdicts(con, pairs, verdicts, "luna") == 2
    rows = con.execute("SELECT node_a, match, status, model FROM identity.llm_verdicts ORDER BY node_a").fetchall()
    assert rows == [("crm|1", True, "ok", "manual:luna"), ("crm|2", False, "ok", "manual:luna"),
                    ("crm|3", None, "unavailable", None)]  # not judged: stays with human review


# ---- the off-switch on the LLM step (identity.llm_enabled) ------------------------------------------
class SpyClient:
    """Stands in for the Anthropic client; records whether anything was sent."""
    def __init__(self):
        self.calls = 0


def _settings(monkeypatch, enabled):
    from trustissues.factory.identity import second_opinion
    s = config.settings()
    monkeypatch.setattr(second_opinion.config, "settings",
                        lambda: {**s, "identity": {**s["identity"], "llm_enabled": enabled}})
    return second_opinion


def test_switched_off_nothing_is_sent_and_every_pair_goes_to_review(con, monkeypatch):
    so = _settings(monkeypatch, False)
    spy = SpyClient()
    monkeypatch.setattr(so, "_ask", lambda *a, **k: (setattr(spy, "calls", spy.calls + 1), {"status": "ok"})[1])
    monkeypatch.setattr(so, "_client", lambda: pytest.fail("no client may be created while llm_enabled is false"))
    out = so.build(con, client=spy)  # even an explicitly passed client is not used
    assert spy.calls == 0 and out["asked"] == 0 and out["unavailable"] == 3
    assert {r[0] for r in con.execute("SELECT reason FROM identity.llm_verdicts").fetchall()} == {
        "llm_enabled is false (merge.md §3)"}


def test_switched_on_the_client_is_asked(con, monkeypatch):
    so = _settings(monkeypatch, True)
    spy = SpyClient()

    def ask(client, model, effort, text):
        client.calls += 1
        return {"status": "ok", "match": False, "probability": 0.1, "deciding_fields": ["name"], "reason": "x", "model": "m"}
    monkeypatch.setattr(so, "_ask", ask)
    assert so.build(con, client=spy)["asked"] == 3 and spy.calls == 3


def test_the_shipped_setting_is_off():
    assert config.settings()["identity"]["llm_enabled"] is False
