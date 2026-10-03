"""Policy search. Unit tests use hand-built chunks; the acceptance test uses the loaded warehouse."""
from datetime import date

import pytest

from trustissues.ask.rag.policy_ingest import Chunk
from trustissues.ask.rag.policy_search import PolicyIndex, in_force, tokenize
from trustissues.common import config


def mk(doc_id, version, eff, section, text, *, sup=None, lang="EN", prov=("ALL",), conf="internal", prods=("card",)):
    meta = {"doc_id": doc_id, "doc_type": "policy", "title": "T", "language": lang, "version": version,
            "effective_date": eff, "superseded_by": sup, "owner": "o", "confidentiality": conf,
            "applies_to_products": list(prods), "applies_to_provinces": list(prov)}
    return Chunk(f"{doc_id}|{version}|{section}|0", doc_id, section, "", f"T {section}\n{text}", text, meta)


V41 = mk("POL-4-V4.1", "4.1", "2025-03-01", "§4.2", "reduced payment 40% for 2 months", sup="POL-4")
V42 = mk("POL-4", "4.2", "2026-03-01", "§4.2", "reduced payment 50% for 3 months")


def test_version_in_force_depends_on_the_as_of_date():
    assert [c.doc_id for c in in_force([V41, V42], date(2026, 9, 28))] == ["POL-4"]
    assert [c.doc_id for c in in_force([V41, V42], date(2025, 12, 1))] == ["POL-4-V4.1"]  # past date: old version
    assert in_force([V41, V42], date(2024, 1, 1)) == []  # nothing in force yet


def test_languages_are_versioned_independently():
    fr = mk("POL-4-FR", "4.2", "2026-03-01", "§4.2", "paiement réduit", lang="FR")
    assert {c.doc_id for c in in_force([V41, V42, fr], date(2026, 9, 28))} == {"POL-4", "POL-4-FR"}


def test_confidentiality_and_province_filters_apply_before_search():
    qc = mk("REG-QC", "1", "2026-01-01", "§1", "no calls on sunday", prov=("QC",))
    secret = mk("SEC", "1", "2026-01-01", "§1", "no calls on sunday", conf="restricted")
    ix = PolicyIndex([qc, secret, V42])
    ids = lambda **k: {c.doc_id for c in ix.eligible(date(2026, 9, 28), **k)}
    assert ids() == {"REG-QC", "POL-4"}  # restricted excluded by default; no province given -> not filtered
    assert ids(province="ON") == {"POL-4"}  # QC-only document hidden from an Ontario question
    assert ids(province="QC", confidentiality={"internal", "restricted"}) == {"REG-QC", "SEC", "POL-4"}


def test_tokenizer_keeps_references_whole():
    t = tokenize("See POL-COLL-004 §4.2 now")
    assert "pol-coll-004" in t and "§4.2" in t and "coll" in t


def test_exact_reference_is_found_by_keyword_search():
    a = mk("POL-9", "1", "2026-01-01", "§9.1", "something about holidays")
    b = mk("POL-COLL-004", "1", "2026-01-01", "§4.2", "something about hardship")
    r = PolicyIndex([a, b]).search("what does POL-COLL-004 say", with_hardship=False)
    assert r.sections[0].doc_id == "POL-COLL-004"


def test_hits_expand_to_one_section_and_prefer_user_language():
    en = mk("POL-5", "1", "2026-01-01", "§5.1", "Calls between 07:00 and 21:00 local time")
    fr = mk("POL-5-FR", "1", "2026-01-01", "§5.1", "Appels entre 07 h et 21 h", lang="FR")
    fr.meta["superseded_by"] = None
    en.meta["superseded_by"] = None
    # make them one family so the same section in two languages collapses to one entry
    en.meta["superseded_by"] = fr.meta["superseded_by"] = "POL-5-FAMILY"
    ix = PolicyIndex([en, fr])
    got = ix.search("calls appels 07:00 21", language="FR", with_hardship=False, english_reference=False).sections
    assert len(got) == 1 and got[0].language == "FR"
    got = ix.search("calls appels 07:00 21", language="EN", with_hardship=False, english_reference=False).sections
    assert len(got) == 1 and got[0].language == "EN"


def test_vector_results_are_fused_with_keyword_results():
    a = mk("A", "1", "2026-01-01", "§1", "alpha beta")
    b = mk("B", "1", "2026-01-01", "§1", "unrelated words")
    only_b = lambda q, pool: {c.chunk_id: 1.0 for c in pool if c.doc_id == "B"}
    r = PolicyIndex([a, b]).search("alpha", vector_fn=only_b, with_hardship=False)
    assert {s.doc_id for s in r.sections} == {"A", "B"}  # B is found only by the vector side


# ---- acceptance: dev benchmark questions that need documents (gold sources from the release) ----
DEV = {
    "BQ-002": ("How many call attempts can we make on a mid-stage account in a week?", {("POL-COLL-001", "§5.2")}),
    "BQ-005": ("Does the National Do Not Call List stop collection calls?", {("POL-COLL-001", "§5.3")}),
    "BQ-019": ("A customer says their hours were cut at work. What must the agent do before asking for payment?",
               {("POL-COLL-004", "§4.2")}),
    "BQ-026": ("How long does the 3-month reduced payment programme last, and what share of the payment is reduced?",
               {("POL-COLL-004", "§4.2")}),
}


@pytest.fixture(scope="module")
def warehouse_index():
    try:
        return PolicyIndex.load()
    except FileNotFoundError:
        pytest.skip("run `python -m trustissues.ask.rag.policy_ingest` first")


@pytest.mark.parametrize("qid", DEV)
def test_dev_questions_retrieve_the_gold_section(warehouse_index, qid):
    question, gold = DEV[qid]
    got = {(s.doc_id, s.section_id) for s in warehouse_index.search(question, config.AS_OF_DATE).sections}
    assert gold <= got


def test_superseded_documents_never_appear_for_the_as_of_date(warehouse_index):
    seen = {s.doc_id for q, _ in DEV.values() for s in warehouse_index.search(q, config.AS_OF_DATE).sections}
    assert not seen & {"POL-COLL-004-V4.1", "SCR-MID-STD-EN-V1.0"}


def test_hardship_rows_come_from_the_table_for_the_as_of_date(warehouse_index):
    rows = warehouse_index.search(DEV["BQ-026"][0], config.AS_OF_DATE).hardship_rows
    red3 = next(r for r in rows if r["program_id"] == "HP-LOAN-RED3")
    assert (red3["payment_reduction_pct"], red3["duration_months"], red3["effective_to"]) == ("0.5", "3", "9999-12-31")


def test_hardship_rows_read_through_the_callers_connection(monkeypatch):
    """The app holds the warehouse for writing; DuckDB refuses a second connection in that process, so the rows must
    be read through the caller's connection when one is given (never by opening another)."""
    import duckdb
    from trustissues.ask.rag import policy_search

    monkeypatch.setattr(policy_search, "connect", lambda **kw: pytest.fail("opened its own connection"))
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA raw")
    con.execute("""CREATE TABLE raw.hardship_programs AS SELECT * FROM (VALUES
        ('HP-1', 'Reduced payment', 'half for 3 months', '§4.2', '["card"]', '2026-01-01', '9999-12-31', now()),
        ('HP-OLD', 'Old plan', 'ended', '§4.2', '["card"]', '2024-01-01', '2025-01-01', now()))
        t(program_id, program_name, description, policy_section, product_types, effective_from, effective_to, _ingested_at)""")
    ix = PolicyIndex([mk("POL-COLL-004", "1", "2026-01-01", "§4.2", "reduced payment programme")])
    rows = ix.whole_corpus("reduced payment", date(2026, 9, 28), con=con).hardship_rows
    assert [r["program_id"] for r in rows] == ["HP-1"]  # only the one in force on the as-of date
    assert con.execute("SELECT 1").fetchone() == (1,)  # the caller's connection is still open


# ---- English is the reference -------------------------------------------------------------------
def test_french_translations_are_not_searched_but_french_only_documents_are():
    from trustissues.ask.rag.policy_search import translation_ids
    en = mk("POL-5", "1", "2026-01-01", "§5.1", "Calls between 07:00 and 21:00 local time")
    fr = mk("POL-5-FR", "1", "2026-01-01", "§5.1", "Appels entre 07 h et 21 h", lang="FR")
    qc = mk("REG-QC", "1", "2026-01-01", "§1", "Au Québec, aucun appel le dimanche", lang="FR", prov=("QC",))
    ix = PolicyIndex([en, fr, qc])
    assert translation_ids(ix.chunks) == {"POL-5-FR"}
    assert {c.doc_id for c in ix.eligible(date(2026, 9, 28))} == {"POL-5", "REG-QC"}
    assert {c.doc_id for c in ix.eligible(date(2026, 9, 28), english_reference=False)} == {"POL-5", "POL-5-FR", "REG-QC"}


# ---- province-specific rules (the Quebec annex) ---------------------------------------------------
def test_province_is_detected_from_the_question():
    from trustissues.ask.rag.jurisdiction import detect_province as d
    assert d("Can we call a customer in Quebec City on Sunday?") == "QC"
    assert d("Peut-on appeler un client à Montréal le dimanche?") == "QC"
    assert d("Is it allowed in QC?") == "QC" and d("Can we call on Sunday?") is None
    assert d("Customer moved from Ontario to Quebec") is None  # two provinces: no guess
    assert d("Call me on the phone") is None  # 'on' is not Ontario


def test_province_specific_document_is_always_included_for_that_province():
    general = mk("POL-1", "1", "2026-01-01", "§5.1", "Sunday calls are limited to 13:00 to 17:00")
    annex = mk("REG-QC", "1", "2026-01-01", "§1", "Au Québec, aucun appel le dimanche", lang="FR", prov=("QC",))
    ix = PolicyIndex([general, annex])
    q = "Sunday calls limited"  # shares no word with the annex
    qc = ix.search(q, province="QC", with_hardship=False).sections
    assert qc[0].doc_id == "REG-QC" and qc[0].pinned and {s.doc_id for s in qc} == {"REG-QC", "POL-1"}
    assert {s.doc_id for s in ix.search(q, province="ON", with_hardship=False).sections} == {"POL-1"}  # not applicable
    assert not any(s.pinned for s in ix.search(q, with_hardship=False).sections)  # no province: normal search only


# ---- retrieval modes (retrievers.py) and whole corpus -------------------------------------------
def test_reranker_order_decides_the_sections():
    a = mk("A", "1", "2026-01-01", "§1", "alpha one")
    b = mk("B", "1", "2026-01-01", "§1", "alpha two")
    b_first = lambda q, cands: sorted(cands, key=lambda c: c.doc_id != "B")
    r = PolicyIndex([a, b]).search("alpha", rerank_fn=b_first, with_hardship=False)
    assert [s.doc_id for s in r.sections] == ["B", "A"]


def test_whole_corpus_returns_every_section_in_force_after_the_filters():
    qc = mk("REG-QC", "1", "2026-01-01", "§1", "no calls on sunday", prov=("QC",))
    other = mk("POL-9", "1", "2026-01-01", "§9.1", "holidays")
    ix = PolicyIndex([V41, V42, qc, other])
    got = ix.whole_corpus("anything", date(2026, 9, 28), with_hardship=False)
    assert {s.doc_id for s in got.sections} == {"POL-4", "REG-QC", "POL-9"}  # the superseded v4.1 never appears
    assert {s.doc_id for s in ix.whole_corpus("x", date(2026, 9, 28), province="ON", with_hardship=False).sections} \
        == {"POL-4", "POL-9"}
    pinned = ix.whole_corpus("x", date(2026, 9, 28), province="QC", with_hardship=False).sections
    assert [s.doc_id for s in pinned if s.pinned] == ["REG-QC"]


def test_unknown_mode_is_refused():
    from trustissues.ask.rag import retrievers
    with pytest.raises(ValueError):
        retrievers.hooks("magic")
    assert retrievers.hooks("bm25") == {}
