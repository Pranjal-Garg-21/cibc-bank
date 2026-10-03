from trustissues.ask.benchmark import compare_tables, parse_tolerance, score

G = "site,rate,promises\nA,61.4,10\nB,58.7,20"


def test_tolerances():
    assert parse_tolerance("±0.5 pts") == ("abs", 0.5)
    assert parse_tolerance("exact count, ±1 CAD") == ("exact",)
    assert parse_tolerance("direction of effect") == ("review",)
    assert parse_tolerance("within a hair")[0] == "unsupported"


def test_order_and_column_names_do_not_matter():
    ours = "site,metric_value,numerator,denominator\nB,58.9,12,20\nA,61.0,6,10"
    assert compare_tables(G, ours, ("abs", 0.5))[0] == "pass"


def test_out_of_tolerance_and_missing_and_extra_rows_fail():
    s, d = compare_tables(G, "site,v,n\nA,60.0,10\nC,1,1", ("abs", 0.5))
    assert s == "fail"
    text = " ".join(d)
    assert "a: ours 60" in text and "missing row b" in text and "extra row c" in text


def test_duplicates_fail_and_count_differences_are_informational():
    assert compare_tables(G, "site,v,n\nA,61.4,10\nA,61.4,10\nB,58.7,20", ("abs", 0.5))[0] == "fail"
    s, d = compare_tables(G, "site,v,n\nA,61.4,11\nB,58.7,20", ("abs", 0.5))
    assert s == "pass" and any("not scored" in x for x in d)


def test_unsupported_tolerance_is_not_a_pass():
    assert compare_tables(G, G, ("unsupported", "weird"))[0] == "unsupported_tolerance"


def test_score_refusals_text_and_ids():
    qs = [{"question_id": "Q1", "tolerance": ""}, {"question_id": "Q2", "tolerance": ""},
          {"question_id": "Q3", "tolerance": "±0.5 pts"}]
    gold = {"Q1": {"gold_answer": "Refuse: outside scope"}, "Q2": {"gold_answer": "Three attempts."},
            "Q3": {"gold_answer": G}}
    sub = [{"question_id": "Q1", "answer": "no", "sql_or_sources": "", "refused": "true"},
           {"question_id": "Q2", "answer": "3", "sql_or_sources": "", "refused": "false"},
           {"question_id": "Q3", "answer": G, "sql_or_sources": "", "refused": "true"}]
    got = {r.question_id: r.status for r in score(qs, gold, sub)}
    assert got == {"Q1": "pass", "Q2": "review", "Q3": "fail"}
