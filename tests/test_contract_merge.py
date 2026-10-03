"""The contract loader: the published contract by default, or an explicit path (P2's addition)."""
import pytest

from trustissues.factory.quality import contract_checks


def test_published_contract_default_and_explicit_paths_agree():
    default = contract_checks.load_contract()
    assert default == contract_checks.load_contract(contract_checks.CONTRACT)
    assert {rule['id'] for rule in default['rules']} == {f'Q{i}' for i in range(12)}


def test_explicit_contract_path_remains_supported_and_validated(tmp_path):
    path = tmp_path / 'contract.yaml'
    path.write_text('product: fixture\nrules: []\n')
    assert contract_checks.load_contract(path) == {'product': 'fixture', 'rules': []}
    path.write_text('- unexpected\n- sequence\n')
    with pytest.raises(ValueError, match='mapping'):
        contract_checks.load_contract(path)


def _rule(rule_id, con):
    rule = next(r for r in contract_checks.load_contract()["rules"] if r["id"] == rule_id)
    return tuple(con.execute(rule["sql"].replace("{g}", "gold_stage").replace("{as_of}", "2026-09-28")).fetchone())


def test_rules_folded_in_from_p2_overdue_totals_and_build_age():
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA gold_stage")
    con.execute("""CREATE TABLE gold_stage.c360_customer AS SELECT * FROM (VALUES
        ('G-1', 100.0, 40.0, 30, current_timestamp::TIMESTAMP - INTERVAL 1 HOUR),    -- reconciles, fresh
        ('G-2', 50.0, 99.0, 10, current_timestamp::TIMESTAMP - INTERVAL 2 DAY))      -- overdue off; built 2 days ago
        t(golden_customer_id, total_balance, total_overdue, max_dpd, refresh_ts)""")
    con.execute("""CREATE TABLE gold_stage.c360_account AS SELECT * FROM (VALUES ('G-1', 100.0, 40.0), ('G-2', 50.0, 9.0))
        t(golden_customer_id, amount_owed, overdue_amount)""")
    assert _rule("Q7", con) == (1, 2)   # Q7: balance matches for both, overdue only for G-1
    assert _rule("Q10", con) == (1, 2)  # Q10: G-2's build is older than 24 hours
