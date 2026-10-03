"""Graph-derived prompt hints over already-approved semantic joins only.

The dictionary graph contains proposals that must not become authorisations.
This serving graph uses the explicit semantic equality allow-list, and never
changes the validator's schema or join policy.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .data.schema_graph import JoinEdge, SchemaGraph, SEMANTIC_JOIN_KEYS

TABLE_WORDS = {
    'gold.semantic_cases': r'\bcases?\b',
    'gold.semantic_promises': r'\b(promises?|ptps?)\b',
    'gold.semantic_contacts': r'\b(calls?|contacts?|sms|emails?)\b',
    'gold.semantic_accounts': r'\b(accounts?|cards?|loans?)\b',
    'gold.semantic_snapshots': r'\b(snapshots?|month.end|roll)\b',
    'gold.semantic_payroll': r'\b(payroll|deposits?)\b',
}


@lru_cache(maxsize=1)
def _graph():
    edges = []
    for left, lc, right, rc in sorted(SEMANTIC_JOIN_KEYS):
        multiplicity = 'many_to_one' if right == 'gold.semantic_cases' else 'unknown'
        edges.append(JoinEdge(left, lc, right, rc, multiplicity))
    return SchemaGraph(edges)


def prompt_join_context(question: str) -> str:
    terminals = [table for table, words in TABLE_WORDS.items() if re.search(words, question, re.I)]
    lines = ['Join hints (guidance only; the validator still enforces the approved equality keys):']
    for left, lc, right, rc in sorted(SEMANTIC_JOIN_KEYS):
        grain = 'many contact/promise rows to one case' if right == 'gold.semantic_cases' else (
            'same account across snapshots; constrain each side to one requested snapshot')
        lines.append(f'- {left}.{lc} = {right}.{rc}: {grain}.')
    if len(terminals) > 1:
        try:
            path = _graph().join_path(terminals)
        except ValueError:
            lines.append('No approved connected path covers the mentioned tables. Do not invent a join.')
        else:
            lines.append('Approximate Steiner path for mentioned tables: ' + '; '.join(
                f'{e.left_table}.{e.left_column} = {e.right_table}.{e.right_column}'
                for e in sorted(path, key=lambda edge: (edge.left_table,edge.left_column,edge.right_table,edge.right_column))))
    lines.append('Two many-side populations joined by case multiply rows. Pre-aggregate each to the needed grain '
                 'before joining, or answer from one population. No customer-level joins are authorised here.')
    return '\n'.join(lines)
