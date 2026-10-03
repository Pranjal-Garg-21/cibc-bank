"""Step 5 of identity: probabilistic matching (Splink, Fellegi-Sunter) for what the exact rules did not settle.

    identity.splink_pairs: every scored pair with match_probability >= review_threshold (0.80), with the
                           comparison level and match weight of every field, so each score can be explained.

Who is compared: every pair has a CRM side (products are linked TO CRM, never to each other), and at least
one side still needs a match:
  - a non-CRM node with no trusted rule path to any CRM record, or
  - a CRM record with no national_id_hash (CRM dedupe beyond the hash). Two CRM records that both carry a
    hash are never compared: a different hash is a conflict, the same hash is already linked.
Nodes held back by an ambiguous or blocked rule match are not re-scored here: a person decides them.

Training uses all nodes (more true matches for EM): u by random sampling, m by EM on two blocking rules
(DOB + postal, name + DOB, full name + postal) so every field is estimated in at least one of them. No labels are needed.
"""
from __future__ import annotations

import time

from trustissues.common import config
from trustissues.common.db import connect

from .graph import components

FIELDS = ["name", "dob", "phone", "postal", "employer"]

# Family guard (deterministic, applied after scoring). Spouses, parents and children share surname, phone and
# address, and those agreements can push a pair over 0.95 on their own. A pair links automatically only if the
# person's OWN identifiers agree: the name (exact, swapped, initial, close, or surname changed with an exact DOB)
# and the DOB (exact, day/month swap, within a month, one digit, or unknown). Anything else goes to the grey zone.
# gamma_name: 8 exact, 7 swapped, 6 initial, 5 first close, 4 same words split differently, 3 surname close,
#             2 first name or initial matches but surname differs, 1 same surname but first name differs, 0 else.
# gamma_dob:  4 exact, 3 swapped, 2 within a month, 1 one digit, 0 different, -1 unknown.
# A record with no name at all (3.9% of bureau files) links only on an exact DOB AND exact postal code; the
# ambiguity check in cluster.py still sends it to review when two different CRM people fit.
# A changed surname (marriage) links on an exact DOB plus a shared phone or postal code: measured against the case
# evidence, 1,107 of 1,110 such pairs are the same person, while "same surname, different first name" pairs are not.
FAMILY_GUARD = ("((gamma_name >= 3 AND gamma_dob <> 0) "
                "OR (gamma_name = 2 AND gamma_dob = 4 AND (gamma_phone >= 1 OR gamma_postal = 2)) "
                "OR (gamma_name = -1 AND gamma_dob = 4 AND gamma_postal = 2))")


def mark_needs_match(con) -> dict:
    """identity.match_input = nodes + needs_match flag."""
    edges = con.execute("SELECT node_a, node_b FROM identity.rule_links WHERE status = 'candidate'").fetchall()
    nodes = [r[0] for r in con.execute("SELECT node_id FROM identity.nodes").fetchall()]
    comp = components(nodes, edges)
    import pyarrow as pa
    con.register("_comp", pa.table({"node_id": list(comp.keys()), "comp": list(comp.values())}))
    con.execute("""CREATE OR REPLACE TABLE identity.match_input AS
        WITH c AS (SELECT * FROM _comp),
        has_crm AS (SELECT c.comp FROM c JOIN identity.nodes n USING (node_id) WHERE n.source_system = 'crm' GROUP BY 1),
        held AS (SELECT node_a AS node_id FROM identity.rule_links WHERE status <> 'candidate'
                 UNION SELECT node_b FROM identity.rule_links WHERE status <> 'candidate')
        SELECT n.node_id AS unique_id, n.*,
               CASE WHEN n.node_id IN (SELECT node_id FROM held) THEN false
                    WHEN n.source_system = 'crm' THEN n.national_id_hash IS NULL
                    ELSE c.comp NOT IN (SELECT comp FROM has_crm) END AS needs_match
        FROM identity.nodes n JOIN c USING (node_id)""")
    con.unregister("_comp")
    return dict(con.execute("SELECT source_system, count(*) FILTER (WHERE needs_match) FROM identity.match_input GROUP BY 1").fetchall())


def _null(col: str) -> dict:
    return {"sql_condition": f'"{col}_l" IS NULL OR "{col}_r" IS NULL', "label_for_charts": "null", "is_null_level": True}


def comparison_specs() -> list[tuple[str, list[dict]]]:
    """(field, comparison levels) for every compared field; also used to explain scores in the review queue."""
    swap = "(year(dob_l) = year(dob_r) AND month(dob_l) = day(dob_r) AND day(dob_l) = month(dob_r))"
    phone_null = "((len(phones_l) = 0 AND phone_mask_regex_l IS NULL) OR (len(phones_r) = 0 AND phone_mask_regex_r IS NULL))"

    def tokens_fit(a: str, b: str) -> str:
        """Compound surnames each system splits its own way ('DE LEON P C' vs 'PRINCESS C DE LEON').
        Every full word of side a is a word of side b, AND either a has 3+ full words, or one of a's initials starts
        a word of b that is not one of a's words (first-name evidence, so 'DE LEON M' never fits sibling 'DE LEON ROSA')."""
        ta, tb = f"string_split(name_key_{a}, ' ')", f"string_split(name_key_{b}, ' ')"
        long_a = f"list_filter({ta}, t -> length(t) > 1)"
        rest_b = f"list_filter({tb}, u -> NOT list_contains({long_a}, u))"
        initial_fits = (f"list_bool_or(list_transform(list_filter({ta}, t -> length(t) = 1), "
                        f"t -> list_bool_or(list_transform({rest_b}, u -> left(u, 1) = t))))")
        return (f"(name_key_{a} IS NOT NULL AND name_key_{b} IS NOT NULL AND len({long_a}) >= 1 "
                f"AND list_has_all({tb}, {long_a}) AND (len({long_a}) >= 3 OR coalesce({initial_fits}, false)))")

    def masked(a: str, b: str) -> str:  # masked side a's visible digits fit one of b's full phones
        return (f"(phone_mask_regex_{a} IS NOT NULL AND "
                f"list_bool_or(list_transform(phones_{b}, p -> regexp_full_match(p, phone_mask_regex_{a}))))")

    return [
        # one comparison for the whole name, so a swapped first/last name counts once, and "same surname,
        # different first name" (a family member) is its own level
        ("name", [
            {"sql_condition": "last_name_l IS NULL OR last_name_r IS NULL", "label_for_charts": "null", "is_null_level": True},
            {"sql_condition": "first_name_l = first_name_r AND last_name_l = last_name_r AND NOT first_is_initial_l",
             "label_for_charts": "first and last exact", "tf_adjustment_column": "last_name", "tf_adjustment_weight": 1.0},
            {"sql_condition": "first_name_l = last_name_r AND last_name_l = first_name_r", "label_for_charts": "first and last swapped"},
            {"sql_condition": "last_name_l = last_name_r AND (first_is_initial_l OR first_is_initial_r) AND first_initial_l = first_initial_r",
             "label_for_charts": "surname exact, initial matches", "tf_adjustment_column": "last_name", "tf_adjustment_weight": 1.0},
            {"sql_condition": "last_name_l = last_name_r AND jaro_winkler_similarity(first_name_l, first_name_r) >= 0.9",
             "label_for_charts": "surname exact, first name close", "tf_adjustment_column": "last_name", "tf_adjustment_weight": 1.0},
            {"sql_condition": f"{tokens_fit('l', 'r')} OR {tokens_fit('r', 'l')}",
             "label_for_charts": "same name words, split differently"},
            {"sql_condition": "jaro_winkler_similarity(last_name_l, last_name_r) >= 0.92 AND (first_name_l = first_name_r OR "
                              "((first_is_initial_l OR first_is_initial_r) AND first_initial_l = first_initial_r))",
             "label_for_charts": "surname close, first name matches"},
            {"sql_condition": "first_name_l = first_name_r OR ((first_is_initial_l OR first_is_initial_r) AND first_initial_l = first_initial_r)",
             "label_for_charts": "first name or initial matches, surname differs"},
            {"sql_condition": "last_name_l = last_name_r", "label_for_charts": "surname exact, first name differs",
             "tf_adjustment_column": "last_name", "tf_adjustment_weight": 1.0},
            {"sql_condition": "ELSE", "label_for_charts": "different"}]),
        ("dob", [
            _null("dob"),
            {"sql_condition": "dob_l = dob_r", "label_for_charts": "exact"},
            {"sql_condition": swap, "label_for_charts": "day/month swapped"},
            {"sql_condition": "abs(date_diff('day', dob_l, dob_r)) <= 31", "label_for_charts": "within a month"},
            {"sql_condition": "damerau_levenshtein(dob_l::VARCHAR, dob_r::VARCHAR) <= 1", "label_for_charts": "one digit differs"},
            {"sql_condition": "ELSE", "label_for_charts": "different"}]),
        ("phone", [
            {"sql_condition": phone_null, "label_for_charts": "null", "is_null_level": True},
            {"sql_condition": "list_has_any(phones_l, phones_r)", "label_for_charts": "a full phone in common"},
            {"sql_condition": f"{masked('l', 'r')} OR {masked('r', 'l')}", "label_for_charts": "masked digits fit"},
            {"sql_condition": "ELSE", "label_for_charts": "different"}]),
        ("postal", [
            _null("postal"),
            {"sql_condition": "postal_l = postal_r", "label_for_charts": "exact"},
            {"sql_condition": "fsa_l = fsa_r", "label_for_charts": "same FSA"},
            {"sql_condition": "ELSE", "label_for_charts": "different"}]),
        ("employer", [
            _null("employer"),
            {"sql_condition": "jaro_winkler_similarity(employer_l, employer_r) >= 0.9", "label_for_charts": "jaro_winkler >= 0.9"},
            {"sql_condition": "ELSE", "label_for_charts": "different"}]),
    ]


def settings(cl):
    comparisons = [cl.CustomComparison(output_column_name=name, comparison_levels=levels) for name, levels in comparison_specs()]
    scope = ("(l.source_system = 'crm' OR r.source_system = 'crm') AND (l.needs_match OR r.needs_match) "
             "AND NOT (l.national_id_hash IS NOT NULL AND r.national_id_hash IS NOT NULL)")
    blocking = [f"l.dob = r.dob AND l.fsa = r.fsa AND {scope}",
                f"l.dob = r.dob AND l.last_name = r.last_name AND {scope}",
                f"l.dob = r.dob AND list_has_any(l.phones, r.phones) AND {scope}",
                f"l.last_name = r.last_name AND l.postal = r.postal AND {scope}",          # DOB typo
                f"l.first_name = r.first_name AND l.postal = r.postal AND {scope}",        # surname changed
                f"l.first_name = r.last_name AND l.last_name = r.first_name AND {scope}"]  # name written surname-first
    return comparisons, blocking


def build(con) -> dict:
    from splink import Linker, SettingsCreator
    import splink.comparison_library as cl
    from splink.backends.duckdb import DuckDBAPI

    s = config.settings()["identity"]
    t0 = time.time()
    need = mark_needs_match(con)
    print("needs a match:", need, flush=True)

    comparisons, blocking = settings(cl)
    # a masked phone ('431519xxx37') becomes one regex over full E.164 numbers: ^\+1431519[0-9]{2}37$
    cols = ("unique_id, source_system, first_name, first_initial, first_is_initial, last_name, name_key, dob, phones, "
            "CASE WHEN phone_masked_prefix IS NOT NULL THEN '^\\+1' || phone_masked_prefix || '[0-9]{' || "
            "(10 - length(phone_masked_prefix) - length(coalesce(phone_masked_suffix, '')))::VARCHAR || '}' || "
            "coalesce(phone_masked_suffix, '') || '$' END AS phone_mask_regex, "
            "postal, fsa, employer, national_id_hash, needs_match")
    # Splink resolves an unqualified table name, so its input lives in the main schema while it runs
    con.execute(f"CREATE OR REPLACE TABLE main.ti_splink_in AS SELECT {cols} FROM identity.match_input")
    db = DuckDBAPI(con)
    sdf = db.register("ti_splink_in", table_name="splink_in")
    linker = Linker(sdf, SettingsCreator(
        link_type="dedupe_only", unique_id_column_name="unique_id",
        comparisons=comparisons, blocking_rules_to_generate_predictions=blocking,
        additional_columns_to_retain=["source_system", "needs_match", "national_id_hash", "first_initial",
                                      "first_is_initial", "phone_mask_regex", "fsa", "name_key"], retain_intermediate_calculation_columns=True,
        retain_matching_columns=True))
    tr = linker.training
    tr.estimate_probability_two_random_records_match(
        ["l.dob = r.dob AND l.last_name = r.last_name AND l.first_name = r.first_name AND l.postal = r.postal"], recall=0.7)
    tr.estimate_u_using_random_sampling(max_pairs=2e7, seed=42)
    tr.estimate_parameters_using_expectation_maximisation("l.dob = r.dob AND l.postal = r.postal", max_pairs=5e6)
    tr.estimate_parameters_using_expectation_maximisation("l.last_name = r.last_name AND l.first_name = r.first_name AND l.dob = r.dob", max_pairs=5e6)
    # DOB is learned where families cannot pollute it: a surname + postal block is full of parents, children and
    # spouses, which teaches EM that a different DOB is normal for a match. Same full name + postal is not.
    tr.estimate_parameters_using_expectation_maximisation(
        "l.first_name = r.first_name AND l.last_name = r.last_name AND l.postal = r.postal", max_pairs=5e6)
    print(f"trained in {time.time() - t0:.0f}s", flush=True)

    pred = linker.inference.predict(threshold_match_probability=s["review_threshold"])
    rel = pred.as_duckdbpyrelation()
    mw = [c for c in rel.columns if c.startswith("bf_") or c.startswith("gamma_")]
    con.register("_pred", rel)
    con.execute(f"""CREATE OR REPLACE TABLE identity.splink_pairs AS
        SELECT least(unique_id_l, unique_id_r) AS node_a, greatest(unique_id_l, unique_id_r) AS node_b,
               match_probability, match_weight, {', '.join(mw) + ',' if mw else ''}
               CASE WHEN match_probability >= {s['link_threshold']} AND {FAMILY_GUARD} THEN 'link' ELSE 'grey' END AS route
        FROM _pred""")
    con.unregister("_pred")
    model_path = config.REPO_ROOT / "reports" / "identity_splink_model.json"
    linker.misc.save_model_to_json(str(model_path), overwrite=True)
    db.delete_tables_created_by_splink_from_db()
    con.execute("DROP TABLE IF EXISTS main.ti_splink_in")
    out = dict(con.execute("SELECT route, count(*) FROM identity.splink_pairs GROUP BY 1").fetchall())
    out["secs"] = round(time.time() - t0)
    return out


def main() -> None:
    con = connect()
    print(build(con))
    con.close()


if __name__ == "__main__":
    main()
