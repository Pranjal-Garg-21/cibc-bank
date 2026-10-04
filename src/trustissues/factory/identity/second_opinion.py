"""Stage 2 matcher: an LLM second opinion on grey-zone pairs only (Splink 0.80-0.95). It never links alone.

    identity.llm_verdicts: one row per grey pair: the serialised records, the verdict, its reason.

Both records are flattened into one tagged sequence (Ditto-style serialisation):
    [SRC] card [NAME] R MITCHELL [DOB] 1988-03-14 [PHONE] 519555xxx42 [POSTAL] N9A4J2 [SEP] [SRC] crm [NAME] ...
The model returns match / probability / deciding fields / a one-line reason as JSON (structured output).

Gate (plan §3.3): a grey pair links only when Splink is in the band AND the LLM says match. A "no" goes to
human review with both explanations. No credentials, an API error, or the cost guard -> status
'unavailable': the pair goes to human review and the records stay separate (the safe default).
Verdicts are cached by a hash of the serialised pair + prompt version, so a rerun never re-asks.

Off-switch: `identity.llm_enabled` (default false). While it is false no client is created and nothing is sent: every
grey pair without a verdict is marked 'unavailable' and goes to human review. The pairs hold unmasked names, dates of
birth and phone numbers, and the team has not agreed which provider may receive them (merge.md §3). Verdicts can still
come from labellers through scripts/second_opinion_manual.py.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from trustissues.common import config
from trustissues.common.db import connect

SYSTEM = """You check whether two customer records from different systems of one Canadian bank describe the SAME person.
Records come from CRM, card, loan and deposit systems and a credit bureau, each formatted its own way:
initials instead of first names, day and month swapped, one-digit typos in dates, masked phone digits (x),
surnames changed by marriage, employer abbreviations. Family members often share an address, phone and surname
but differ in first name or date of birth: that is NOT a match.
A wrong merge shows a customer someone else's debt, so say match only when the evidence supports it."""

SCHEMA = {
    "type": "object",
    "properties": {
        "match": {"type": "boolean"},
        "probability": {"type": "number"},
        "deciding_fields": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["match", "probability", "deciding_fields", "reason"],
    "additionalProperties": False,
}

SERIAL_SQL = """
    '[SRC] ' || n.source_system
    || ' [NAME] ' || coalesce(nullif(concat_ws(' ', n.first_name, n.middle_initials, n.last_name), ''), '?')
    || ' [DOB] ' || coalesce(n.dob::VARCHAR, '?')
    || ' [PHONE] ' || coalesce(nullif(array_to_string(n.phones, ','), ''),
                               n.phone_masked_prefix || 'xxx' || coalesce(n.phone_masked_suffix, ''), '?')
    || ' [POSTAL] ' || coalesce(n.postal, '?')
    || CASE WHEN n.employer IS NOT NULL THEN ' [EMPLOYER] ' || n.employer ELSE '' END"""


def _client():
    try:
        import anthropic
        return anthropic.Anthropic(max_retries=3)
    except Exception as e:  # SDK missing or no credential source at all
        print(f"second opinion unavailable: {e}")
        return None


def _ask(client, model: str, effort: str, text: str) -> dict:
    import anthropic
    try:
        resp = client.beta.messages.create(
            model=model, max_tokens=2000, system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": f"Same person?\n{text}"}])
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        return {"status": "unavailable", "reason": f"no credentials: {e.__class__.__name__}"}
    except anthropic.APIError as e:
        return {"status": "error", "reason": f"{e.__class__.__name__}: {e}"[:300]}
    except Exception as e:  # e.g. no credential source at all: the SDK raises before any request
        return {"status": "unavailable", "reason": f"{e.__class__.__name__}: {e}"[:300]}
    if resp.stop_reason == "refusal":
        return {"status": "error", "reason": "refused"}
    body = next((b.text for b in resp.content if b.type == "text"), None)
    try:
        out = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return {"status": "error", "reason": "unparseable response"}
    out.update(status="ok", model=resp.model)
    return out


def build(con, client=None) -> dict:
    s = config.settings()["identity"]
    version = s["llm_prompt_version"]
    con.execute("""CREATE TABLE IF NOT EXISTS identity.llm_verdicts (pair_key VARCHAR, node_a VARCHAR, node_b VARCHAR,
        serialised VARCHAR, match BOOLEAN, probability DOUBLE, deciding_fields VARCHAR, reason VARCHAR, status VARCHAR,
        model VARCHAR, prompt_version VARCHAR, asked_ts TIMESTAMP)""")
    grey = con.execute(f"""
        SELECT p.node_a, p.node_b, ({SERIAL_SQL.replace('n.', 'a.')}) || ' [SEP] ' || ({SERIAL_SQL.replace('n.', 'b.')}) AS text
        FROM identity.splink_pairs p JOIN identity.nodes a ON a.node_id = p.node_a JOIN identity.nodes b ON b.node_id = p.node_b
        WHERE p.route = 'grey' ORDER BY p.match_probability DESC, p.node_a, p.node_b""").fetchall()
    todo, cached = [], 0
    done = {r[0] for r in con.execute("SELECT pair_key FROM identity.llm_verdicts WHERE status = 'ok'").fetchall()}
    for a, b, text in grey:
        key = hashlib.md5(f"{version}|{text}".encode()).hexdigest()
        if key in done:
            cached += 1
        else:
            todo.append((key, a, b, text))
    con.execute("DELETE FROM identity.llm_verdicts WHERE status <> 'ok'")  # retry failures next time

    over = todo[s["llm_max_pairs"]:]
    todo = todo[:s["llm_max_pairs"]]
    enabled = s.get("llm_enabled", False)
    client = client if client is not None else (_client() if todo and enabled else None)
    results: list[tuple] = []
    if not enabled:
        results = [(k, a, b, t, {"status": "unavailable", "reason": "llm_enabled is false (merge.md §3)"})
                   for k, a, b, t in todo]
    elif client is not None and todo:
        first = _ask(client, s["llm_model"], s["llm_effort"], todo[0][3])
        if first["status"] != "ok":  # one probe: do not fire thousands of calls that will all fail
            print(f"second opinion unavailable: {first['reason']}")
            results = [(k, a, b, t, {"status": "unavailable", "reason": first["reason"]}) for k, a, b, t in todo]
        else:
            with ThreadPoolExecutor(max_workers=s["llm_workers"]) as ex:
                rest = list(ex.map(lambda r: _ask(client, s["llm_model"], s["llm_effort"], r[3]), todo[1:]))
            results = [(k, a, b, t, v) for (k, a, b, t), v in zip(todo, [first] + rest)]
    else:
        results = [(k, a, b, t, {"status": "unavailable", "reason": "no LLM client"}) for k, a, b, t in todo]
    results += [(k, a, b, t, {"status": "unavailable", "reason": "over llm_max_pairs cost guard"}) for k, a, b, t in over]

    rows = [(k, a, b, t, v.get("match"), v.get("probability"), json.dumps(v.get("deciding_fields") or []),
             v.get("reason"), v["status"], v.get("model"), version) for k, a, b, t, v in results]
    if rows:
        con.executemany("INSERT INTO identity.llm_verdicts VALUES (?,?,?,?,?,?,?,?,?,?,?, now())", rows)
        _audit(con, [r for r in rows if r[8] == "ok"])
    return {"grey_pairs": len(grey), "cached": cached, "asked": sum(1 for r in rows if r[8] == "ok"),
            "unavailable": sum(1 for r in rows if r[8] == "unavailable"), "errors": sum(1 for r in rows if r[8] == "error")}


def _audit(con, rows) -> None:
    """Every LLM call goes in gold.audit_log (kind identity_pair)."""
    exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gold' "
                         "AND table_name = 'audit_log'").fetchone()[0]
    if not exists or not rows:
        return
    con.executemany("""INSERT INTO gold.audit_log (ts, user_role, user_id, kind, input_text, sources, prompt, answer, model, prompt_version)
        VALUES (now(), 'system', 'identity_pipeline', 'identity_pair', ?, ?, ?, ?, ?, ?)""",
                    [(r[0], json.dumps([r[1], r[2]]), r[3], json.dumps({"match": r[4], "probability": r[5], "reason": r[7]}),
                      r[9], r[10]) for r in rows])


def main() -> None:
    con = connect()
    print(build(con))
    con.close()


if __name__ == "__main__":
    main()
