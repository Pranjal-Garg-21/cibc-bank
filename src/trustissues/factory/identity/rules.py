"""Steps 3-4 of identity: exact rule passes, surest first. Deterministic SQL, no AI.

    identity.rule_links (node_a, node_b, method, confidence, status, detail)

Rules (design doc §4; plan §9 "Rule passes"):
  crm_pointer      CRM's own duplicate_of_crm_id                       CRM <-> CRM
  national_id      same national_id_hash (exists only in CRM)          CRM <-> CRM
  cif              deposit CIF = bureau bank_subject_ref               deposit <-> bureau
  name_dob_phone   same DOB + last name + compatible first name + one full phone in common
  dob_name_postal  same DOB + last name + compatible first name + same postal code

"Compatible first name": equal, or one side is an initial that matches the other's first letter
(deposit holders are surname + initials only). Phone or postal alone never links; both name rules need
an exact DOB. The two name rules run CRM <-> CRM (dedupe) and every other source <-> CRM.

status:
  candidate        trusted link, goes to clustering
  blocked_conflict a strong identifier disagrees (a pointer / hash / CIF pair whose DOBs are clearly different)
  ambiguous        one non-CRM node matches two or more different CRM people: no tie-break, sent to review
A node with a blocked or ambiguous rule match is NOT sent on to Splink: a person decides.
"""
from __future__ import annotations

from trustissues.common import config
from trustissues.common.db import connect

from .graph import components


def _first_ok(a: str, b: str) -> str:
    return (f"({a}.first_name IS NOT NULL AND {b}.first_name IS NOT NULL AND ({a}.first_name = {b}.first_name OR "
            f"(({a}.first_is_initial OR {b}.first_is_initial) AND {a}.first_initial = {b}.first_initial)))")


def dob_clearly_different(a: str, b: str, spread_days: int) -> str:
    """Both DOBs known, not a day/month swap, and further apart than `spread_days`."""
    return (f"({a} IS NOT NULL AND {b} IS NOT NULL AND abs(date_diff('day', {a}, {b})) > {spread_days} "
            f"AND NOT (year({a}) = year({b}) AND month({a}) = day({b}) AND day({a}) = month({b})))")


def build(con) -> dict:
    s = config.settings()["identity"]
    conf, spread = s["rule_confidence"], s["max_dob_spread_days"]
    con.execute("""CREATE OR REPLACE TABLE identity.rule_links (node_a VARCHAR, node_b VARCHAR, method VARCHAR,
                   confidence DOUBLE, status VARCHAR, detail VARCHAR)""")

    def add(method: str, sql: str, check_dob: bool = False) -> None:
        # sql selects (node_a, node_b, dob_a, dob_b) with node_a < node_b
        status = (f"CASE WHEN {dob_clearly_different('dob_a', 'dob_b', spread)} THEN 'blocked_conflict' ELSE 'candidate' END"
                  if check_dob else "'candidate'")
        detail = ("CASE WHEN dob_a IS DISTINCT FROM dob_b THEN 'dob ' || coalesce(dob_a::VARCHAR, '?') || ' vs ' || "
                  "coalesce(dob_b::VARCHAR, '?') END")
        con.execute(f"""INSERT INTO identity.rule_links
            SELECT node_a, node_b, '{method}', {conf[method]}, {status}, {detail}
            FROM ({sql}) WHERE node_a < node_b
            QUALIFY row_number() OVER (PARTITION BY node_a, node_b) = 1""")
        # a pair already linked by a surer rule keeps that rule only
        con.execute(f"""DELETE FROM identity.rule_links r WHERE method = '{method}' AND EXISTS (
            SELECT 1 FROM identity.rule_links x WHERE x.node_a = r.node_a AND x.node_b = r.node_b AND x.method <> '{method}')""")

    crm = "(SELECT * FROM identity.nodes WHERE source_system = 'crm')"
    add("crm_pointer", f"""SELECT least(a.node_id, b.node_id) node_a, greatest(a.node_id, b.node_id) node_b,
            CASE WHEN a.node_id < b.node_id THEN a.dob ELSE b.dob END dob_a, CASE WHEN a.node_id < b.node_id THEN b.dob ELSE a.dob END dob_b
        FROM {crm} a JOIN {crm} b ON b.source_key = a.crm_duplicate_of""", check_dob=True)
    add("national_id", f"""SELECT a.node_id node_a, b.node_id node_b, a.dob dob_a, b.dob dob_b
        FROM {crm} a JOIN {crm} b ON a.national_id_hash = b.national_id_hash AND a.node_id < b.node_id""", check_dob=True)
    add("cif", """SELECT b.node_id node_a, d.node_id node_b, b.dob dob_a, d.dob dob_b
        FROM identity.nodes d JOIN identity.nodes b ON b.source_system = 'bureau' AND d.source_system = 'deposit'
         AND b.source_key = d.source_key""", check_dob=True)

    # name rules: every pair has at least one CRM side
    con.execute("""CREATE OR REPLACE TEMP TABLE node_phone AS
        SELECT node_id, source_system, dob, unnest(phones) AS phone FROM identity.nodes WHERE dob IS NOT NULL""")
    add("name_dob_phone", f"""SELECT least(a.node_id, b.node_id) node_a, greatest(a.node_id, b.node_id) node_b, a.dob dob_a, b.dob dob_b
        FROM node_phone pa JOIN node_phone pb ON pa.phone = pb.phone AND pa.dob = pb.dob AND pa.node_id <> pb.node_id
         AND pa.source_system = 'crm' AND (pb.source_system <> 'crm' OR pa.node_id < pb.node_id)
        JOIN identity.nodes a ON a.node_id = pa.node_id JOIN identity.nodes b ON b.node_id = pb.node_id
        WHERE a.last_name = b.last_name AND {_first_ok('a', 'b')}""")
    add("dob_name_postal", f"""SELECT least(a.node_id, b.node_id) node_a, greatest(a.node_id, b.node_id) node_b, a.dob dob_a, b.dob dob_b
        FROM {crm} a JOIN identity.nodes b ON a.dob = b.dob AND a.postal = b.postal AND a.last_name = b.last_name
         AND a.node_id <> b.node_id AND (b.source_system <> 'crm' OR a.node_id < b.node_id)
        WHERE {_first_ok('a', 'b')}""")

    # a different national ID is a conflict on the strongest identifier: no weaker rule may link the pair
    con.execute("""UPDATE identity.rule_links r SET status = 'blocked_conflict', detail = 'different national_id_hash'
        FROM identity.nodes a, identity.nodes b
        WHERE a.node_id = r.node_a AND b.node_id = r.node_b AND a.national_id_hash <> b.national_id_hash""")
    mark_ambiguous(con)
    return {f"{m}/{st}": n for m, st, n in con.execute(
        "SELECT method, status, count(*) FROM identity.rule_links GROUP BY ALL ORDER BY 1, 2").fetchall()}


def mark_ambiguous(con) -> None:
    """A non-CRM node whose rule matches reach two or more DIFFERENT CRM people is ambiguous.

    "Different" is judged after CRM dedupe: two CRM records already joined by a trusted CRM <-> CRM rule are
    one person, so matching both of them is not ambiguous.
    """
    crm_edges = con.execute("""SELECT node_a, node_b FROM identity.rule_links WHERE status = 'candidate'
        AND starts_with(node_a, 'crm|') AND starts_with(node_b, 'crm|')""").fetchall()
    crm_nodes = [r[0] for r in con.execute("SELECT node_id FROM identity.nodes WHERE source_system = 'crm'").fetchall()]
    group = components(crm_nodes, crm_edges)
    con.execute("CREATE OR REPLACE TEMP TABLE crm_group (node_id VARCHAR, grp BIGINT)")
    _load_groups(con, group)
    con.execute("""UPDATE identity.rule_links r SET status = 'ambiguous',
            detail = coalesce(detail || '; ', '') || 'matches ' || a.n || ' different CRM people'
        FROM (SELECT other, count(DISTINCT g.grp) n FROM (
                SELECT CASE WHEN starts_with(node_a, 'crm|') THEN node_b ELSE node_a END other,
                       CASE WHEN starts_with(node_a, 'crm|') THEN node_a ELSE node_b END crm_node
                FROM identity.rule_links WHERE status = 'candidate'
                  AND starts_with(node_a, 'crm|') <> starts_with(node_b, 'crm|')) l
              JOIN crm_group g ON g.node_id = l.crm_node GROUP BY other HAVING count(DISTINCT g.grp) > 1) a
        WHERE r.status = 'candidate' AND starts_with(r.node_a, 'crm|') <> starts_with(r.node_b, 'crm|')
          AND (r.node_a = a.other OR r.node_b = a.other)""")


def _load_groups(con, group: dict) -> None:
    import pyarrow as pa
    tbl = pa.table({"node_id": list(group.keys()), "grp": list(group.values())})
    con.register("_grp_arrow", tbl)
    con.execute("INSERT INTO crm_group SELECT node_id, grp FROM _grp_arrow")
    con.unregister("_grp_arrow")


def main() -> None:
    con = connect()
    for k, v in build(con).items():
        print(f"{k:40s} {v:>10,}")
    con.close()


if __name__ == "__main__":
    main()
