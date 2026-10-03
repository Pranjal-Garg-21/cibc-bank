"""Step 8 of identity: joint holders and co-borrowers are relationships, never merges.

    cannot_link_pairs(con)          node pairs that must never end up in one person (used by clustering)
    build_gold(con)                 gold_stage.customer_relationship between two golden IDs

Deposit `joint_holder_ref` is a CIF: the other holder's node is deposit|<cif> or, if that person holds no
deposit account of their own, bureau|<cif>. Loan `co_borrower_ref` is a borrower number: loan|<ref>.
These links are never used as identity evidence.
"""
from __future__ import annotations

PAIRS_SQL = """
    WITH j AS (SELECT DISTINCT cif10, lpad(joint_holder_ref, 10, '0') AS other_cif, deposit_account_id
               FROM curated.deposit_accounts_current
               WHERE joint_holder_ref IS NOT NULL AND lpad(joint_holder_ref, 10, '0') <> cif10)
    SELECT 'deposit|' || j.cif10 AS node_a, n.node_id AS node_b, 'joint_holder' AS relationship_type, j.deposit_account_id AS account_id
    FROM j JOIN identity.nodes n ON n.node_id = 'deposit|' || j.other_cif
    UNION ALL
    SELECT 'deposit|' || j.cif10, n.node_id, 'joint_holder', j.deposit_account_id
    FROM j JOIN identity.nodes n ON n.node_id = 'bureau|' || j.other_cif
    UNION ALL
    SELECT DISTINCT 'loan|' || l.src_customer_ref, n.node_id, 'co_borrower', l.loan_id
    FROM curated.loan_accounts_current l JOIN identity.nodes n ON n.node_id = 'loan|' || l.co_borrower_ref
    WHERE l.co_borrower_ref IS NOT NULL AND l.co_borrower_ref <> l.src_customer_ref
"""


def build_pairs(con) -> int:
    con.execute(f"CREATE OR REPLACE TABLE identity.relationship_pairs AS {PAIRS_SQL}")
    return con.execute("SELECT count(*) FROM identity.relationship_pairs").fetchone()[0]


def cannot_link_pairs(con) -> list[tuple[str, str]]:
    return con.execute("SELECT DISTINCT node_a, node_b FROM identity.relationship_pairs").fetchall()


def build_gold(con) -> int:
    con.execute("""CREATE OR REPLACE TABLE gold_stage.customer_relationship AS
        SELECT DISTINCT xa.golden_customer_id AS golden_customer_id_a, xb.golden_customer_id AS golden_customer_id_b,
               r.relationship_type, r.account_id, r.node_a AS source_node_a, r.node_b AS source_node_b
        FROM identity.relationship_pairs r
        JOIN gold_stage.customer_xref xa ON xa.node_id = r.node_a
        JOIN gold_stage.customer_xref xb ON xb.node_id = r.node_b""")
    return con.execute("SELECT count(*) FROM gold_stage.customer_relationship").fetchone()[0]
