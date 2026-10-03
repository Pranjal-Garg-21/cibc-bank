import pytest

from trustissues.governance import masking as M


def test_classification_from_sensitivity_and_names():
    assert M.classify("cardholder_name_raw") == "direct_pii"
    assert M.classify("citizenship_status") == "protected"
    assert M.classify("full_name") == "direct_pii"           # gold column, classified by name
    assert M.classify("date_of_birth") == "direct_pii"
    assert M.classify("contact_hour_local") == "none" or M.classify("contact_hour_local") == "unclassified"
    assert M.classify("totally_unknown_thing") == "unclassified"  # fail closed
    assert M.classify("risk_score") == "none" and M.classify("golden_customer_id") == "none"


def test_roles_get_different_views():
    cols = ["golden_customer_id", "full_name", "std_phone_primary_e164", "gender_code", "risk_score"]
    row = [("G-000001", "Jane Smith", "+14165551234", "F", 0.8)]
    c, r = M.mask_rows(cols, row, "agent")
    assert c == ["golden_customer_id", "full_name", "std_phone_primary_e164", "risk_score"]  # gender dropped
    assert r[0][1] == "J*** S***" and r[0][2].endswith("1234") and "416" not in r[0][2]
    c, r = M.mask_rows(cols, row, "team_lead")
    assert r[0][1] == "***" and r[0][2] == "***" and r[0][3] == 0.8
    c, r = M.mask_rows(cols, row, "analyst")
    assert "full_name" not in c and "std_phone_primary_e164" not in c


def test_unknown_role_is_an_error():
    with pytest.raises(ValueError):
        M.mask_rows(["a"], [(1,)], "intern")
    with pytest.raises(ValueError):
        M.mask_text("x", "intern")


def test_unknown_columns_are_masked_not_shown():
    c, r = M.mask_rows(["mystery_blob"], [("secret",)], "analyst")
    assert r == [("***",)]


def test_none_stays_none():
    c, r = M.mask_rows(["full_name"], [(None,)], "team_lead")
    assert r == [(None,)]


@pytest.mark.parametrize("col,val,expect", [
    ("email", "jane.smith@example.com", "j***@example.com"),
    ("date_of_birth", "1984-07-02", "1984-**-**"),
    ("postal_code", "M5V 2T6", "M5V ***"),
    ("phone", "(416) 555-1234", "***-***-1234"),
])
def test_partial_forms(col, val, expect):
    assert M.partial(col, val) == expect


def test_free_text_identifiers_replaced():
    t = "Called 416-555-1234, email jane@x.ca, SIN 123 456 789, card 4111 1111 1111 1111, postal M5V 2T6. Promised $95."
    out = M.mask_text(t, "agent")
    assert "416" not in out and "jane@" not in out and "123 456" not in out and "4111" not in out and "M5V" not in out
    # the same placeholders as text_controls.scrub(), the single scrubber: a SIN-style number is [ID]
    assert "[PHONE]" in out and "[EMAIL]" in out and "[ID]" in out and "[CARD]" in out and "[POSTAL]" in out
    assert "Promised $95" in out
    assert M.mask_text("Promised $95 on 14 Oct", "analyst") == "Promised $95 on 14 Oct"


def test_input_not_mutated_and_frame_helper():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"golden_customer_id": ["G-1"], "full_name": ["Jane Smith"], "gender_code": ["F"]})
    out = M.mask_frame(df, "team_lead")
    assert list(out.columns) == ["golden_customer_id", "full_name"] and out.loc[0, "full_name"] == "***"
    assert df.loc[0, "full_name"] == "Jane Smith" and "gender_code" in df.columns


def test_answer_card_masking():
    from datetime import date
    from trustissues.ask.types import AnswerCard
    card = AnswerCard("q", "Call 416-555-1234", "metric", date(2026, 9, 28), columns=("full_name", "n"),
                      rows=(("Jane Smith", 3),))
    out = M.mask_answer_card(card, "team_lead")
    assert out.rows == (("***", 3),) and "[PHONE]" in out.answer and card.rows == (("Jane Smith", 3),)
