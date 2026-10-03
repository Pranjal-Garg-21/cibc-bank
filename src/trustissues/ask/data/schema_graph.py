"""Join proposals with multiplicity and explicit identity-crosswalk requirements.

Dictionary relationships are proposals, not proof of uniqueness. A join path is not executable SQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import networkx as nx
from networkx.algorithms.approximation import steiner_tree
import openpyxl


@dataclass(frozen=True)
class JoinEdge:
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    multiplicity: str = "unknown"
    required_filter: str | None = None

    @property
    def weight(self) -> float:
        return {"one_to_one": 1.0, "many_to_one": 2.0, "one_to_many": 2.0}.get(self.multiplicity, 10.0)


SEMANTIC_JOIN_KEYS = frozenset({
    ("gold.semantic_promises", "case_id", "gold.semantic_cases", "case_id"),
    ("gold.semantic_contacts", "case_id", "gold.semantic_cases", "case_id"),
    ("gold.semantic_snapshots", "account_id", "gold.semantic_snapshots", "account_id"),
})


class SchemaGraph:
    def __init__(self, edges: Iterable[JoinEdge] = ()):
        self.graph = nx.MultiGraph()
        self.unresolved: list[tuple] = []
        for edge in edges:
            self.add(edge)

    def add(self, edge: JoinEdge) -> None:
        self.graph.add_edge(edge.left_table, edge.right_table, edge=edge, weight=edge.weight)

    def join_path(self, tables: Iterable[str]) -> tuple[JoinEdge, ...]:
        terminals = set(tables)
        if not terminals:
            return ()
        if not terminals <= set(self.graph):
            raise ValueError(f"Unknown graph tables: {terminals - set(self.graph)}")
        first = next(iter(terminals))
        component = nx.node_connected_component(self.graph, first)
        if not terminals <= component:
            raise ValueError("No connected join path")
        if len(terminals) == 1:
            return ()
        tree = steiner_tree(self.graph.subgraph(component), terminals, weight="weight", method="kou")
        return tuple(data["edge"] for _, _, data in tree.edges(data=True))

    def approved_keys(self) -> frozenset[tuple[str, str, str, str]]:
        # Conditional crosswalk edges cannot be approved by key equality alone.
        return frozenset((e.left_table, e.left_column, e.right_table, e.right_column)
                         for _, _, data in self.graph.edges(data=True)
                         for e in [data["edge"]] if not e.required_filter and e.multiplicity != "unknown")

    @classmethod
    def from_dictionary(cls, path: Path | str, *, primary_keys: dict[str, str] | None = None) -> "SchemaGraph":
        declared = primary_keys or {}
        result = cls()
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            rows = workbook["Relationships"].iter_rows(values_only=True)
            header = next(rows)
            for values in rows:
                row = dict(zip(header, values))
                a, ac, b, bc = (row.get(k) for k in ("From table", "From column", "To table", "To column"))
                if not all(isinstance(x, str) and x for x in (a, ac, b, bc)):
                    continue
                if not bc.replace("_", "").isalnum():
                    result.unresolved.append((a, ac, b, bc))
                    continue
                cardinality = "many_to_one" if declared.get(b) == bc else "unknown"
                result.add(JoinEdge(f"gold.{a}", ac, f"gold.{b}", bc, cardinality))
        finally:
            workbook.close()
        # Source keys are system-local: every proposed edge requires this discriminator.
        for table, col, system in (("customers", "crm_customer_id", "crm"),
                                   ("card_accounts", "src_customer_ref", "card"),
                                   ("loan_accounts", "src_customer_ref", "loan"),
                                   ("deposit_accounts", "src_customer_ref", "deposit"),
                                   ("external", "bank_subject_ref", "bureau")):
            result.add(JoinEdge(f"gold.{table}", col, "gold.customer_xref", "source_key", "many_to_one",
                                f"customer_xref.source_system = '{system}'"))
        result.add(JoinEdge("gold.customer_xref", "golden_customer_id", "gold.c360_customer", "golden_customer_id", "many_to_one"))
        return result
