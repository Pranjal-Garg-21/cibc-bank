"""Step 7 of identity: trusted links -> people. Writes gold_stage.customer_xref and the review queue.

Trusted links, strongest first:
  exact rules (status candidate)                  confidence from settings (1.0 / 0.99 / 0.98)
  human match                                     1.0
  Splink >= 0.95                                  its match probability
  Splink 0.80-0.95 AND the LLM said match         its match probability (method llm_confirmed)

Clustering is a constrained union: links are added strongest first, and a link is REFUSED when the person it
would create breaks a rule:
  - two different national_id_hash values
  - DOBs clearly different (more than max_dob_spread_days apart, a day/month swap excepted)
  - more than max_crm_per_cluster CRM records
  - a joint-holder / co-borrower pair, or a pair a person marked no_match, would end up together
A refused link goes to review (reason cluster_conflict); the cluster is split along its weakest links.
Splink links from one non-CRM record to two or more different CRM people are ambiguous: none are used.

Golden IDs are stable: a cluster keeps the ID of the registered anchor it contains (oldest first); a new
cluster's anchor is its oldest CRM record (else its smallest node id), numbered in a deterministic order.
"""
from __future__ import annotations

import time
from datetime import date

from trustissues.common import config

from .graph import ConstrainedUnion, components
from .relationships import cannot_link_pairs

METHOD_RANK = {"crm_pointer": 0, "national_id": 1, "cif": 2, "human": 3, "name_dob_phone": 4,
               "dob_name_postal": 5, "splink": 6, "llm_confirmed": 7}


def make_combine(max_crm: int, max_spread: int):
    """Summary = (hashes, min_dob, max_dob, n_crm, tags). tags: {(pair_no, side)} from cannot-link pairs."""

    def combine(a, b):
        ha, mna, mxa, ca, ta = a
        hb, mnb, mxb, cb, tb = b
        hashes = ha | hb if ha and hb else (ha or hb)
        if hashes and len(hashes) > 1:
            return None
        if ca + cb > max_crm:
            return None
        mn = min(x for x in (mna, mnb) if x is not None) if mna is not None or mnb is not None else None
        mx = max(x for x in (mxa, mxb) if x is not None) if mxa is not None or mxb is not None else None
        if mn is not None and mx - mn > max_spread and not _swap_only(a, b):
            return None
        if ta and tb:
            small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if any((k, 1 - side) in big for k, side in small):
                return None
        tags = ta | tb if ta and tb else (ta or tb)
        return (hashes, mn, mx, ca + cb, tags)

    return combine


def _swap_only(a, b) -> bool:
    """Two single DOBs that are a day/month swap of each other are the same date written two ways."""
    if a[1] != a[2] or b[1] != b[2] or a[1] is None or b[1] is None:
        return False
    da, db = date.fromordinal(a[1]), date.fromordinal(b[1])
    return da.year == db.year and da.month == db.day and da.day == db.month


def gather_edges(con, link_threshold: float) -> list[tuple]:
    """(confidence, method, node_a, node_b) for every trusted link, before conflict checks."""
    con.execute("""CREATE OR REPLACE TEMP TABLE trusted AS
        SELECT node_a, node_b, method, confidence FROM identity.rule_links WHERE status = 'candidate'
        UNION ALL SELECT node_a, node_b, 'human', 1.0 FROM identity.human_decisions WHERE decision = 'match' AND still_valid
        UNION ALL SELECT p.node_a, p.node_b, CASE WHEN p.route = 'link' THEN 'splink' ELSE 'llm_confirmed' END, p.match_probability
            FROM identity.splink_pairs p LEFT JOIN identity.llm_verdicts v ON v.node_a = p.node_a AND v.node_b = p.node_b AND v.status = 'ok'
            WHERE p.route = 'link' OR (p.route = 'grey' AND v.match)""")
    # a person's no_match / unresolved overrides any automatic link
    con.execute("""DELETE FROM trusted t WHERE EXISTS (SELECT 1 FROM identity.human_decisions h
        WHERE h.node_a = t.node_a AND h.node_b = t.node_b AND h.still_valid AND h.decision IN ('no_match', 'unresolved'))""")
    # Splink ambiguity: a non-CRM node whose links (rule or probabilistic) reach two CRM people that no trusted
    # CRM <-> CRM link joins. Its probabilistic links are dropped and reviewed; its rule links were checked in rules.py.
    crm_edges = con.execute("""SELECT node_a, node_b FROM trusted WHERE starts_with(node_a, 'crm|') AND starts_with(node_b, 'crm|')""").fetchall()
    crm_nodes = [r[0] for r in con.execute("SELECT node_id FROM identity.nodes WHERE source_system = 'crm'").fetchall()]
    import pyarrow as pa
    grp = components(crm_nodes, crm_edges)
    con.register("_crm_grp", pa.table({"node_id": list(grp), "grp": list(grp.values())}))
    con.execute("""CREATE OR REPLACE TEMP TABLE splink_ambiguous AS
        WITH l AS (SELECT CASE WHEN starts_with(node_a, 'crm|') THEN node_b ELSE node_a END other,
                          CASE WHEN starts_with(node_a, 'crm|') THEN node_a ELSE node_b END crm_node, node_a, node_b, method
                   FROM trusted WHERE starts_with(node_a, 'crm|') <> starts_with(node_b, 'crm|')),
        amb AS (SELECT other FROM l JOIN _crm_grp g ON g.node_id = l.crm_node GROUP BY other
                HAVING count(DISTINCT g.grp) > 1 AND bool_or(method IN ('splink', 'llm_confirmed')))
        SELECT DISTINCT node_a, node_b FROM l WHERE other IN (SELECT other FROM amb) AND method IN ('splink', 'llm_confirmed')""")
    con.unregister("_crm_grp")
    con.execute("""DELETE FROM trusted t WHERE t.method IN ('splink', 'llm_confirmed') AND EXISTS (
        SELECT 1 FROM splink_ambiguous a WHERE a.node_a = t.node_a AND a.node_b = t.node_b)""")
    # one row per pair: the strongest method (a rule and Splink can both propose the same pair)
    con.execute("""CREATE OR REPLACE TEMP TABLE trusted AS SELECT * FROM trusted
        QUALIFY row_number() OVER (PARTITION BY node_a, node_b ORDER BY method IN ('splink', 'llm_confirmed'), confidence DESC) = 1""")
    rows = con.execute("SELECT confidence, method, node_a, node_b FROM trusted").fetchall()
    rows.sort(key=lambda r: (-r[0], METHOD_RANK.get(r[1], 9), r[2], r[3]))
    return rows


def build(con) -> dict:
    s = config.settings()["identity"]
    t0 = time.time()
    edges = gather_edges(con, s["link_threshold"])

    summaries = {}
    for node_id, h, dob, system in con.execute(
            "SELECT node_id, national_id_hash, dob, source_system FROM identity.nodes").fetchall():
        o = dob.toordinal() if dob else None
        summaries[node_id] = (frozenset([h]) if h else None, o, o, 1 if system == "crm" else 0, None)
    pairs = cannot_link_pairs(con) + con.execute("""SELECT node_a, node_b FROM identity.human_decisions
        WHERE decision = 'no_match' AND still_valid""").fetchall()
    for k, (a, b) in enumerate(pairs):
        for node, side in ((a, 0), (b, 1)):
            if node in summaries:
                h, mn, mx, c, tags = summaries[node]
                summaries[node] = (h, mn, mx, c, (tags or frozenset()) | {(k, side)})

    uf = ConstrainedUnion(summaries, make_combine(s["max_crm_per_cluster"], s["max_dob_spread_days"]))
    accepted, refused = [], []
    for conf, method, a, b in edges:
        r = uf.union(a, b)
        (refused if r == "refused" else accepted).append((a, b, method, conf))
    print(f"union: {len(accepted):,} accepted, {len(refused):,} refused ({time.time() - t0:.0f}s)", flush=True)

    import pyarrow as pa
    nodes = list(summaries)
    con.register("_cl", pa.table({"node_id": nodes, "root": [uf.find(n) for n in nodes]}))
    con.execute("CREATE OR REPLACE TABLE identity.clusters AS SELECT node_id, root FROM _cl")
    con.unregister("_cl")
    schema = pa.schema([("node_a", pa.string()), ("node_b", pa.string()), ("method", pa.string()), ("confidence", pa.float64())])
    con.register("_acc", pa.table({k: [e[i] for e in accepted] for i, k in enumerate(schema.names)}, schema=schema))
    con.execute("CREATE OR REPLACE TABLE identity.accepted_links AS SELECT * FROM _acc")
    con.unregister("_acc")
    con.execute("""CREATE OR REPLACE TABLE identity.review_candidates (node_a VARCHAR, node_b VARCHAR, reason VARCHAR, detail VARCHAR)""")
    if refused:
        con.executemany("INSERT INTO identity.review_candidates VALUES (?, ?, 'cluster_conflict', ?)",
                        [(a, b, f"{m} link ({c:.3f}) would merge records that conflict") for a, b, m, c in refused])
    con.execute("""INSERT INTO identity.review_candidates
        SELECT node_a, node_b, status, detail FROM identity.rule_links WHERE status IN ('ambiguous', 'blocked_conflict')
        UNION ALL SELECT node_a, node_b, 'splink_ambiguous', 'probabilistic match to two or more different CRM people' FROM splink_ambiguous
        UNION ALL SELECT p.node_a, p.node_b, CASE WHEN v.status = 'ok' THEN 'llm_disagrees' ELSE 'grey_zone_no_llm' END,
               'splink ' || round(p.match_probability, 3)::VARCHAR
        FROM identity.splink_pairs p LEFT JOIN identity.llm_verdicts v ON v.node_a = p.node_a AND v.node_b = p.node_b
        WHERE p.route = 'grey' AND NOT coalesce(v.match, false)""")
    # pairs a person called 'unresolved' stay separate and stay out of the queue until the evidence changes
    assign_ids(con, s["golden_id_digits"])
    write_xref(con)
    return {"accepted_links": len(accepted), "refused_links": len(refused),
            **dict(con.execute("SELECT status, count(*) FROM gold_stage.customer_xref GROUP BY 1").fetchall()),
            "people": con.execute("SELECT count(DISTINCT golden_customer_id) FROM gold_stage.customer_xref").fetchone()[0],
            "secs": round(time.time() - t0)}


def assign_ids(con, digits: int) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS identity.golden_id_registry (anchor_node_id VARCHAR PRIMARY KEY,
        golden_customer_id VARCHAR, assigned_ts TIMESTAMP)""")
    con.execute("""CREATE OR REPLACE TEMP TABLE cl_anchor AS
        WITH m AS (SELECT c.root, c.node_id, n.source_system, n.record_created_ts, r.golden_customer_id reg_id
                   FROM identity.clusters c JOIN identity.nodes n USING (node_id)
                   LEFT JOIN identity.golden_id_registry r ON r.anchor_node_id = c.node_id)
        SELECT root,
               -- reuse: the registered anchor in this cluster with the smallest (oldest) golden id
               min(reg_id) AS reused_id,
               arg_min(node_id, (source_system <> 'crm', coalesce(record_created_ts, TIMESTAMP '9999-12-31'), node_id)) AS anchor
        FROM m GROUP BY root""")
    nxt = con.execute("""SELECT coalesce(max(TRY_CAST(substr(golden_customer_id, 3) AS BIGINT)), 0) FROM identity.golden_id_registry""").fetchone()[0]
    con.execute(f"""INSERT INTO identity.golden_id_registry
        SELECT a.anchor, 'G-' || lpad(({nxt} + row_number() OVER (ORDER BY n.source_system <> 'crm',
                   coalesce(n.record_created_ts, TIMESTAMP '9999-12-31'), a.anchor))::VARCHAR, {digits}, '0'), now()
        FROM cl_anchor a JOIN identity.nodes n ON n.node_id = a.anchor
        WHERE a.reused_id IS NULL""")
    con.execute("""CREATE OR REPLACE TABLE identity.cluster_ids AS
        SELECT a.root, coalesce(a.reused_id, r.golden_customer_id) AS golden_customer_id
        FROM cl_anchor a LEFT JOIN identity.golden_id_registry r ON r.anchor_node_id = a.anchor""")


def write_xref(con) -> None:
    """gold_stage.customer_xref: every source customer exactly once (contract rule 2)."""
    con.execute("""CREATE OR REPLACE TABLE gold_stage.customer_xref AS
        WITH best AS (  -- the strongest accepted link touching each node
            SELECT node_id, method, confidence, other FROM (
                SELECT node_a node_id, method, confidence, node_b other FROM identity.accepted_links
                UNION ALL SELECT node_b, method, confidence, node_a FROM identity.accepted_links)
            QUALIFY row_number() OVER (PARTITION BY node_id ORDER BY confidence DESC, method, other) = 1),
        review AS (SELECT node_id, count(*) n FROM (SELECT node_a node_id FROM identity.review_candidates
                   UNION ALL SELECT node_b FROM identity.review_candidates) GROUP BY 1),
        unresolved AS (SELECT node_a node_id FROM identity.human_decisions WHERE decision = 'unresolved' AND still_valid
                       UNION SELECT node_b FROM identity.human_decisions WHERE decision = 'unresolved' AND still_valid),
        blocked AS (SELECT node_a node_id FROM identity.review_candidates WHERE reason IN ('cluster_conflict', 'blocked_conflict')
                    UNION SELECT node_b FROM identity.review_candidates WHERE reason IN ('cluster_conflict', 'blocked_conflict')),
        size AS (SELECT root, count(*) cluster_size FROM identity.clusters GROUP BY 1)
        SELECT i.golden_customer_id, n.source_system, n.source_key,
               coalesce(b.method, 'singleton') AS match_method,
               b.confidence AS match_confidence,
               CASE WHEN s.cluster_size > 1 THEN 'linked'
                    WHEN n.node_id IN (SELECT node_id FROM unresolved) THEN 'unresolved'
                    WHEN n.node_id IN (SELECT node_id FROM blocked) THEN 'blocked_conflict'
                    WHEN r.n > 0 THEN 'in_review'
                    ELSE 'singleton' END AS status,
               n.node_id, b.other AS linked_via, s.cluster_size, coalesce(r.n, 0) AS open_review_items
        FROM identity.nodes n JOIN identity.clusters c USING (node_id) JOIN identity.cluster_ids i USING (root)
        JOIN size s USING (root) LEFT JOIN best b USING (node_id) LEFT JOIN review r USING (node_id)""")
