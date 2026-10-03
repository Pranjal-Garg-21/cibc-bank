"""Identity governance guarantees (plan §8): phone alone never links; joint holders never merge;
a conflict blocks a weaker rule; one record matching two different people is not linked; cluster conflict checks.

Runs the real rules + clustering SQL on a tiny in-memory warehouse of FAKE nodes.
"""
from datetime import date

import duckdb
import pytest

from trustissues.factory.identity import cluster, review_queue, rules
from trustissues.factory.identity.graph import ConstrainedUnion
from trustissues.factory.identity.nodes import NODE_COLUMNS


def node(node_id, first=None, last=None, dob=None, phones=(), postal=None, nid=None, dup_of=None, created=None):
    system, key = node_id.split("|")
    return (node_id, system, key, first, first[0] if first else None, bool(first) and len(first) == 1, None,
            last, None, dob, 1 if dob else 0, list(phones), None, None, postal, postal[:3] if postal else None,
            None, nid, dup_of, created, None, 1)


def warehouse(nodes, relationship_pairs=()):
    con = duckdb.connect(":memory:")
    for s in ("identity", "gold_stage", "gold", "curated"):
        con.execute(f"CREATE SCHEMA {s}")
    con.execute(f"CREATE TABLE identity.nodes ({NODE_COLUMNS})")
    con.executemany(f"INSERT INTO identity.nodes VALUES ({','.join('?' * 22)})", nodes)
    con.execute("CREATE TABLE identity.splink_pairs (node_a VARCHAR, node_b VARCHAR, match_probability DOUBLE, route VARCHAR)")
    con.execute("CREATE TABLE identity.llm_verdicts (node_a VARCHAR, node_b VARCHAR, match BOOLEAN, probability DOUBLE, reason VARCHAR, status VARCHAR)")
    con.execute("CREATE TABLE identity.relationship_pairs (node_a VARCHAR, node_b VARCHAR, relationship_type VARCHAR, account_id VARCHAR)")
    for a, b in relationship_pairs:
        con.execute("INSERT INTO identity.relationship_pairs VALUES (?, ?, 'joint_holder', 'X')", [a, b])
    return con


def run(con, tmp_path):
    rules.build(con)
    review_queue.load_decisions(con, tmp_path / "none.csv")
    cluster.build(con)
    return {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT node_id, golden_customer_id, status, match_method FROM gold_stage.customer_xref").fetchall()}


D1, D2 = date(1980, 5, 17), date(1991, 2, 3)


def test_phone_alone_never_links(tmp_path):
    con = warehouse([node("crm|A", "JOHN", "SMITH", D1, ["+14165550101"], "M5V1A1"),
                     node("card|1", "MARY", "SMITH", D2, ["+14165550101"], "M5V1A1")])  # spouse: same phone and address
    x = run(con, tmp_path)
    assert x["crm|A"][0] != x["card|1"][0]


def test_name_dob_phone_links_and_initial_is_compatible(tmp_path):
    con = warehouse([node("crm|A", "JOHN", "SMITH", D1, ["+14165550101"]),
                     node("card|1", "J", "SMITH", D1, ["+14165550101"])])
    x = run(con, tmp_path)
    assert x["crm|A"][0] == x["card|1"][0] and x["card|1"][2] == "name_dob_phone"


def test_joint_holders_never_merge(tmp_path):
    # the same person would link through their own deposit, but a joint holder pair is forbidden
    con = warehouse([node("crm|A", "ANNE", "ROY", D1, ["+15145550101"], "H2X1Y1"),
                     node("deposit|0000000001", "A", "ROY", D1, ["+15145550101"], "H2X1Y1"),
                     node("bureau|0000000001", "ANNE", "ROY", D1, [], "H2X1Y1")],
                    relationship_pairs=[("deposit|0000000001", "crm|A")])
    x = run(con, tmp_path)
    assert x["crm|A"][0] != x["deposit|0000000001"][0]


def test_dob_conflict_blocks_strong_rule(tmp_path):
    con = warehouse([node("crm|A", "LUC", "ROY", D1, nid="h1", created="2019-01-01"),
                     node("crm|B", "LUC", "ROY", D2, nid="h1", created="2020-01-01")])  # same hash, clearly different DOB
    x = run(con, tmp_path)
    assert x["crm|A"][0] != x["crm|B"][0]
    assert x["crm|A"][1] == "blocked_conflict"


def test_two_hashes_never_merge(tmp_path):
    # card matches both CRM records by rule; they carry different national IDs -> not one person
    con = warehouse([node("crm|A", "PAT", "LEE", D1, ["+16045550101"], "V5K1A1", nid="h1"),
                     node("crm|B", "PAT", "LEE", D1, ["+16045550102"], "V5K1A1", nid="h2"),
                     node("card|1", "PAT", "LEE", D1, ["+16045550101"], "V5K1A1")])
    x = run(con, tmp_path)
    assert x["crm|A"][0] != x["crm|B"][0]


def test_one_record_two_people_is_ambiguous(tmp_path):
    con = warehouse([node("crm|A", "SAM", "KIM", D1, postal="T2P1A1", nid="h1"),
                     node("crm|B", "SAM", "KIM", D1, postal="T2P1A1", nid="h2"),
                     node("loan|1", "SAM", "KIM", D1, postal="T2P1A1")])
    x = run(con, tmp_path)
    assert x["loan|1"][1] in ("in_review", "blocked_conflict") and x["loan|1"][0] not in (x["crm|A"][0], x["crm|B"][0])


def test_every_node_once_and_stable_ids(tmp_path):
    nodes = [node("crm|A", "JOHN", "SMITH", D1, ["+14165550101"], created="2018-01-01"),
             node("card|1", "J", "SMITH", D1, ["+14165550101"]), node("loan|9", "ZED", "ZULU", D2)]
    con = warehouse(nodes)
    first = run(con, tmp_path)
    assert len(first) == 3 and first["loan|9"][1] == "singleton"
    second = run(con, tmp_path)  # rerun: same IDs
    assert {k: v[0] for k, v in first.items()} == {k: v[0] for k, v in second.items()}


def test_constrained_union_refuses_weakest_link():
    combine = cluster.make_combine(max_crm=5, max_spread=400)
    o = lambda d: d.toordinal()
    s = {"a": (frozenset(["h1"]), o(D1), o(D1), 1, None), "b": (None, o(D1), o(D1), 0, None),
         "c": (frozenset(["h2"]), o(D1), o(D1), 1, None)}
    uf = ConstrainedUnion(s, combine)
    assert uf.union("a", "b") == "merged"     # strong link first
    assert uf.union("b", "c") == "refused"    # weaker link would join two national IDs
    assert uf.find("c") != uf.find("a")


def test_day_month_swap_is_not_a_conflict():
    combine = cluster.make_combine(max_crm=5, max_spread=30)
    a = (None, date(1980, 3, 11).toordinal(), date(1980, 3, 11).toordinal(), 1, None)
    b = (None, date(1980, 11, 3).toordinal(), date(1980, 11, 3).toordinal(), 0, None)
    assert combine(a, b) is not None


def test_more_than_max_crm_is_a_conflict():
    combine = cluster.make_combine(max_crm=5, max_spread=400)
    assert combine((None, None, None, 3, None), (None, None, None, 3, None)) is None


# ----- Splink family guard (gamma codes: see splink_model.FAMILY_GUARD) -----
@pytest.mark.parametrize("name,dob,phone,postal,links", [
    (8, 4, -1, -1, True),    # exact name and DOB
    (6, 2, -1, -1, True),    # initial matches, DOB within a month (typo)
    (1, 4, 2, 2, False),     # same surname, different first name, same DOB/phone/address: twins or siblings
    (1, 0, 2, 2, False),     # same surname and household, different first name and DOB: family member
    (8, 0, 2, 2, False),     # same name, clearly different DOB: father and son
    (2, 4, 1, 2, True),      # surname changed, exact DOB, phone and address agree
    (2, 2, 2, 2, False),     # surname changed but DOB only close: not enough
    (-1, 4, -1, 2, True),    # no name on the bureau file: exact DOB and exact postal
    (-1, 4, -1, 1, False),   # no name, only the FSA agrees
])
def test_family_guard(name, dob, phone, postal, links):
    from trustissues.factory.identity.splink_model import FAMILY_GUARD
    got = duckdb.connect().execute(f"SELECT {FAMILY_GUARD} FROM (SELECT {name} gamma_name, {dob} gamma_dob, "
                                   f"{phone} gamma_phone, {postal} gamma_postal)").fetchone()[0]
    assert got is links


if __name__ == "__main__":
    pytest.main([__file__])
