"""The models behind the policy search hooks, and the choice of retrieval mode (design D10, D11, D12).

Modes (config `policy_rag.mode`, or `mode=` on run_policy):

    bm25            keyword search only (the original build)
    hybrid          keyword + vector search, merged by reciprocal rank fusion (D11)
    hybrid_rerank   hybrid, then a cross-encoder reorders the candidates (D12)
    whole_corpus    no search: every section in force on the as-of date goes to the model

Both models run locally, so search needs no network call and costs nothing per question:
- embeddings: `intfloat/multilingual-e5-small` (EN + FR in one space, D8, D10)
- reranker:   `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual cross-encoder)

The corpus is 51 chunks, so the vectors are a small matrix held in memory (cached on disk by text hash) instead of a
Chroma collection (D9): same result, one fewer service. Models load once per process, on first use.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from trustissues.common import config

from .policy_ingest import Chunk

MODES = ("bm25", "hybrid", "hybrid_rerank", "whole_corpus")
DEFAULTS = {"mode": "hybrid_rerank", "embedding_model": "intfloat/multilingual-e5-small",
            "reranker_model": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", "n_candidates": 20, "top_k": 5}


def settings() -> dict:
    return {**DEFAULTS, **(config.settings().get("policy_rag") or {})}


def _passage(c: Chunk) -> str:
    return f"{c.meta['title']} {c.section_id} {c.section_title}. {c.text}"


class LocalEmbedder:
    """`vector_fn` for PolicyIndex.search: cosine similarity between the question and each chunk."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.e5 = "e5" in model_name.lower()  # e5 models expect "query: " / "passage: " prefixes
        self._cache_path = config.warehouse_path().parent / "rag" / "policy_vectors.json"
        self._cache: dict[str, list[float]] = self._load_cache()

    def _load_cache(self) -> dict:
        try:
            d = json.loads(self._cache_path.read_text(encoding="utf-8"))
            return d["vectors"] if d.get("model") == self.model_name else {}
        except (OSError, ValueError, KeyError):
            return {}

    def _key(self, c: Chunk) -> str:
        return hashlib.sha256(_passage(c).encode()).hexdigest()[:20]

    def _vectors(self, chunks: list[Chunk]):
        import numpy as np

        missing = [c for c in chunks if self._key(c) not in self._cache]
        if missing:
            texts = [("passage: " if self.e5 else "") + _passage(c) for c in missing]
            for c, v in zip(missing, self.model.encode(texts, normalize_embeddings=True)):
                self._cache[self._key(c)] = [round(float(x), 6) for x in v]
            try:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                self._cache_path.write_text(json.dumps({"model": self.model_name, "vectors": self._cache}),
                                            encoding="utf-8")
            except OSError:
                pass  # the cache only saves time
        return np.array([self._cache[self._key(c)] for c in chunks])

    def __call__(self, query: str, chunks: list[Chunk]) -> dict[str, float]:
        q = self.model.encode([("query: " if self.e5 else "") + query], normalize_embeddings=True)[0]
        sims = self._vectors(chunks) @ q
        return {c.chunk_id: float(s) for c, s in zip(chunks, sims)}


class CrossEncoderReranker:
    """`rerank_fn` for PolicyIndex.search: reads the question and each candidate together and reorders them."""

    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder

        self.model_name = model_name
        self.model = CrossEncoder(model_name)
        self.last_scores: dict[str, float] = {}

    def __call__(self, query: str, cands: list[Chunk]) -> list[Chunk]:
        if not cands:
            return cands
        scores = self.model.predict([(query, _passage(c)) for c in cands])
        self.last_scores = {c.chunk_id: float(s) for c, s in zip(cands, scores)}
        return [c for _, c in sorted(zip(scores, cands), key=lambda x: -x[0])]


@lru_cache(maxsize=4)
def embedder(model_name: str | None = None) -> LocalEmbedder:
    return LocalEmbedder(model_name or settings()["embedding_model"])


@lru_cache(maxsize=4)
def reranker(model_name: str | None = None) -> CrossEncoderReranker:
    return CrossEncoderReranker(model_name or settings()["reranker_model"])


def hooks(mode: str | None = None) -> dict:
    """search() keyword arguments for a mode. whole_corpus has no hooks: it skips search altogether."""
    mode = mode or settings()["mode"]
    if mode not in MODES:
        raise ValueError(f"unknown policy retrieval mode {mode!r}: one of {MODES}")
    kw: dict = {}
    if mode in ("hybrid", "hybrid_rerank"):
        kw["vector_fn"] = embedder()
    if mode == "hybrid_rerank":
        kw["rerank_fn"] = reranker()
    return kw
