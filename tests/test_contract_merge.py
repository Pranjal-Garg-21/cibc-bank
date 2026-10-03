"""The merged contract loader supports the P1 default and P2 explicit-path APIs."""
import pytest

from trustissues.factory.quality import contract_checks


def test_published_contract_default_and_explicit_paths_agree():
    default = contract_checks.load_contract()
    assert default == contract_checks.load_contract(contract_checks.CONTRACT)
    assert {rule['id'] for rule in default['rules']} == {f'Q{i}' for i in range(10)}


def test_explicit_contract_path_remains_supported_and_validated(tmp_path):
    path = tmp_path / 'contract.yaml'
    path.write_text('product: fixture\nrules: []\n')
    assert contract_checks.load_contract(path) == {'product': 'fixture', 'rules': []}
    path.write_text('- unexpected\n- sequence\n')
    with pytest.raises(ValueError, match='mapping'):
        contract_checks.load_contract(path)
