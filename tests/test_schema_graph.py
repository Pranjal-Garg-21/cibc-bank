import pytest

from trustissues.ask.data.schema_graph import JoinEdge, SchemaGraph
from trustissues.common.config import REPO_ROOT


def test_steiner_path_prefers_known_multiplicity():
    graph = SchemaGraph([JoinEdge("a","id","b","id","many_to_one"),
                         JoinEdge("b","id","c","id","many_to_one"),
                         JoinEdge("a","other","c","other","unknown")])
    path = graph.join_path(["a", "c"])
    assert len(path) == 2
    assert all(e.multiplicity == "many_to_one" for e in path)
    with pytest.raises(ValueError):
        graph.join_path(["a", "missing"])


def test_dictionary_crosswalk_edges_require_system_discriminator():
    graph = SchemaGraph.from_dictionary(REPO_ROOT / "docs" / "CIBC Hackathon - Data Dictionary.xlsx",
                                        primary_keys={"agents": "agent_id", "collections_cases": "case_id"})
    assert graph.unresolved
    path = graph.join_path(["gold.card_accounts", "gold.c360_customer"])
    assert any(e.required_filter == "customer_xref.source_system = 'card'" for e in path)
    assert not any(key[0] == "gold.card_accounts" and key[2] == "gold.customer_xref" for key in graph.approved_keys())
