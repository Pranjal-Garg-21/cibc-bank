

def test_parse_json_repairs_an_unquoted_answer_value():
    from trustissues.common.llm import parse_json
    ok = parse_json('{"answer": Yes, the agent must read it.}, "evidence": [{"source": "S1", "quote": "read it"}], "not_found": false}')
    assert ok["answer"] == "Yes, the agent must read it." and ok["evidence"][0]["source"] == "S1" and ok["not_found"] is False
    quoted = parse_json('{"answer": yes, read: "This call may be recorded.", "evidence": [], "not_found": false}')
    assert quoted["answer"].startswith("yes, read:") and quoted["evidence"] == []
