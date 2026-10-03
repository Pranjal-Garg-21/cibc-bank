"""Local catalogue retrieval. Scores identify metrics, never formulas or permissions.

Download the model explicitly with scripts/setup_router.py. Request-time loading
uses cached files only; a missing optional model leaves the deterministic router
available. Profiles come from catalogue metadata, never the labelled eval set.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from trustissues.common.config import CONFIG_DIR, REPO_ROOT
from .data.metric_templates import METRICS

MODEL = "BAAI/bge-small-en-v1.5"
CACHE_DIR = REPO_ROOT / "warehouse" / "cache" / "router_embeddings"
PROFILES = CONFIG_DIR / "metric_match_profiles.json"
MIN_SCORE = 0.65
MIN_MARGIN = 0.055


@dataclass(frozen=True)
class Candidate:
    metric_id: str
    name: str
    score: float


@dataclass(frozen=True)
class MetricMatch:
    candidates: tuple[Candidate, ...]
    metric_id: str | None

    @property
    def clarification(self) -> str:
        names = [c.name for c in self.candidates[:2]]
        return "Which metric do you mean: " + " or ".join(names) + "?"


def decide(candidates: tuple[Candidate, ...]) -> MetricMatch:
    """A weak or close match is a clarification, never an automatic selection."""
    ranked = tuple(sorted(candidates, key=lambda c: -c.score))
    if any(c.metric_id not in METRICS for c in ranked):
        raise ValueError("Matcher returned a metric outside the catalogue")
    chosen = None
    if len(ranked) >= 2 and ranked[0].score >= MIN_SCORE and ranked[0].score - ranked[1].score >= MIN_MARGIN:
        chosen = ranked[0].metric_id
    return MetricMatch(ranked[:2], chosen)


@lru_cache(maxsize=1)
def _index():
    import numpy as np
    from fastembed import TextEmbedding

    profiles = json.loads(PROFILES.read_text())["metrics"]
    if {p["metric_id"] for p in profiles} != METRICS:
        raise ValueError("Metric profiles must cover exactly the approved catalogue")
    model = TextEmbedding(model_name=MODEL, cache_dir=str(CACHE_DIR), threads=2,
                          providers=['CPUExecutionProvider'], local_files_only=True)
    passages = []
    owners = []
    for profile in profiles:
        # Multiple catalogue fields, not synthetic paraphrases tailored to evaluation.
        for text in (profile['name'] + ': ' + profile['definition'],
                     profile['example'], profile['name'] + ': ' + ', '.join(profile['synonyms'])):
            passages.append(text)
            owners.append(profile)
    vectors = np.asarray(list(model.passage_embed(passages)))
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return model, owners, vectors


@lru_cache(maxsize=512)
def match(question: str) -> MetricMatch | None:
    """None means the optional local model is unavailable; no provider is called."""
    try:
        model, owners, vectors = _index()
    except (ImportError, OSError, ValueError):
        return None
    import numpy as np
    query = next(model.query_embed(question))
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    scores = vectors @ query
    best = {}
    for owner, score in zip(owners, scores):
        mid = owner['metric_id']
        candidate = Candidate(mid, owner['name'], float(score))
        if mid not in best or candidate.score > best[mid].score:
            best[mid] = candidate
    return decide(tuple(best.values()))
