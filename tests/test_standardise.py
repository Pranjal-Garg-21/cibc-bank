from trustissues.factory.curate.standardise import (
    normalise_employer, parse_dob, parse_name, parse_phone, parse_postal)


# ----- DOB -----
def test_dob_unambiguous_formats():
    for raw in ["1975-09-12", "1975/09/12", "19750912", "Sep 12 1975", "12 Sep 1975"]:
        r = parse_dob(raw)
        assert r["dob_iso"] == "1975-09-12" and not r["dob_ambiguous"], raw


def test_dob_day_over_12_resolves():
    assert parse_dob("13-01-1990")["dob_iso"] == "1990-01-13"      # DD-MM, day 13
    assert parse_dob("01/13/1990")["dob_iso"] == "1990-01-13"      # MM/DD, day 13


def test_dob_ambiguous_keeps_both_readings():
    r = parse_dob("07/12/1992")
    assert r["dob_ambiguous"] and {r["dob_iso"], r["dob_alt_iso"]} == {"1992-07-12", "1992-12-07"}
    assert r["dob_iso"] == "1992-07-12"  # '/' is month-first
    assert parse_dob("07-12-1992")["dob_iso"] == "1992-12-07"  # '-' is day-first


def test_dob_same_day_month_not_ambiguous():
    assert not parse_dob("05/05/1990")["dob_ambiguous"]


def test_dob_invalid_or_empty():
    assert parse_dob("31/02/1990")["dob_iso"] is None
    assert parse_dob("")["dob_iso"] is None and parse_dob(None)["dob_iso"] is None


# ----- phone -----
def test_phone_formats_to_e164():
    for raw in ["6045336260", "(604) 533-6260", "604-533-6260", "604.533.6260", "+1 (604) 533 6260", "16045336260"]:
        assert parse_phone(raw)["phone_e164"] == "+16045336260", raw


def test_masked_phone_keeps_visible_digits_and_is_not_a_number():
    r = parse_phone("450672xxx81")
    assert r["phone_e164"] is None and r["phone_masked"]
    assert r["phone_prefix"] == "450672" and r["phone_suffix"] == "81"


def test_bad_phone():
    assert parse_phone("12345")["phone_e164"] is None
    assert parse_phone("0000000000")["phone_e164"] is None


# ----- names -----
def test_name_accents_and_case():
    r = parse_name("Raphaël Tremblay")
    assert (r["first"], r["last"]) == ("RAPHAEL", "TREMBLAY")


def test_name_comma_wins():
    r = parse_name("NOWAK, OKSANA ZOFIA")
    assert (r["last"], r["first"], r["middle_initials"]) == ("NOWAK", "OKSANA", "Z")
    assert (parse_name("Ball, Tracy")["last"], parse_name("Ball, Tracy")["first"]) == ("BALL", "TRACY")


def test_name_initials_kept_as_initials():
    r = parse_name("J. LANGLOIS")
    assert (r["first"], r["last"], r["first_is_initial"]) == ("J", "LANGLOIS", True)


def test_name_trailing_initials_mean_last_first():
    r = parse_name("DYER D C")
    assert (r["last"], r["first"], r["middle_initials"]) == ("DYER", "D", "C")


def test_name_middle_initial_mixed_case():
    r = parse_name("Marie T Lamontagne")
    assert (r["first"], r["middle_initials"], r["last"]) == ("MARIE", "T", "LAMONTAGNE")


def test_all_caps_two_words_is_ambiguous_without_hint():
    r = parse_name("GUO YAN")
    assert r["last"] is None and r["name_key"] == "GUO YAN"
    assert parse_name("GUO YAN", order="last_first")["last"] == "GUO"
    assert parse_name("GUO YAN", caps_default="last_first")["last"] == "GUO"
    assert parse_name("Guo Yan", caps_default="last_first")["last"] == "YAN"  # mixed case ignores the caps default


def test_name_key_is_order_insensitive():
    assert parse_name("SMITH JOHN")["name_key"] == parse_name("John Smith")["name_key"]


def test_replacement_character_is_dropped_not_guessed():
    assert parse_name("Rapha�l Tremblay")["first"] == "RAPHAL"


# ----- postal -----
def test_postal():
    for raw in ["M5V 2T6", "m5v2t6", "M5V-2T6"]:
        assert parse_postal(raw) == {"postal": "M5V2T6", "fsa": "M5V"}
    assert parse_postal("12345")["postal"] is None
    assert parse_postal("D0D 0D0")["postal"] is None  # D, F, I, O, Q, U never start a Canadian code


# ----- employer -----
def test_employer_variants_collapse():
    a = normalise_employer("Wildrose Machining Corp.")
    assert a == normalise_employer("Wildrose Machining Limited") == "WILDROSE MACHINING"
    assert normalise_employer("Silverbirch Media Inc. (Saguenay)") == "SILVERBIRCH MEDIA"
    assert normalise_employer("Cascade Engineering Ltée") == normalise_employer("Cascade Engineering Ltd")
