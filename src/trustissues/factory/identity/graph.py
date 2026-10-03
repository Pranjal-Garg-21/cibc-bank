"""Graph helpers for identity: plain connected components, and the constrained union used for clustering."""
from __future__ import annotations

from typing import Callable, Hashable, Iterable


def components(nodes: list[Hashable], edges: Iterable[tuple[Hashable, Hashable]]) -> dict[Hashable, int]:
    """node -> component number (sparse, fast for millions of nodes)."""
    import numpy as np
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    index = {n: i for i, n in enumerate(nodes)}
    pairs = [(index[a], index[b]) for a, b in edges if a in index and b in index]
    n = len(nodes)
    rows = np.fromiter((a for a, _ in pairs), dtype=np.int64, count=len(pairs))
    cols = np.fromiter((b for _, b in pairs), dtype=np.int64, count=len(pairs))
    m = coo_matrix((np.ones(len(pairs), dtype=np.int8), (rows, cols)), shape=(n, n))
    _, labels = connected_components(m, directed=False)
    return {node: int(labels[i]) for node, i in index.items()}


class ConstrainedUnion:
    """Union-find that refuses a merge when the merged group would break a rule.

    Each group carries a summary (any object). `combine(sa, sb)` returns the merged summary, or None if the
    merge is not allowed. Edges are offered strongest first, so when a group would become inconsistent the
    weakest links are the ones refused: the cluster is "split along its weakest links".
    """

    def __init__(self, summaries: dict[Hashable, object], combine: Callable[[object, object], object | None]):
        self.parent = {n: n for n in summaries}
        self.summary = dict(summaries)
        self.combine = combine

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b) -> str:
        """-> 'merged' | 'same' (already one group) | 'refused'."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return "same"
        merged = self.combine(self.summary[ra], self.summary[rb])
        if merged is None:
            return "refused"
        self.parent[rb] = ra
        self.summary[ra] = merged
        del self.summary[rb]
        return "merged"
