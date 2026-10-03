"""Policy retrieval: filter first, then search, then expand hits to whole sections.

Order of operations (design D4, D11, D12, D5):

1. Filter  - drop chunks that are not the version in force on the as-of date, that the role may
             not see, that do not apply to the product/province asked about, or that are French
             translations of an English document (English is the reference).
2. Search  - BM25 keyword search over the surviving chunks. An optional vector search is merged
             in with reciprocal rank fusion when a `vector_fn` is supplied (hybrid, D11).
3. Rerank  - optional `rerank_fn` re-orders the candidates (D12). Default keeps search order.
4. Expand  - each hit becomes its full parent section, de-duplicated; when the same section
             exists in EN and FR the user's language is kept (D12).
5. Attach  - hardship programme rows are read from `hardship_programs` by SQL for the version
             valid on the as-of date. Where text and table disagree the table wins (D5).

Nothing here calls an LLM. The vector and rerank steps are hooks because the embedding provider
and reranker model are still open (D10, D12).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from rank_bm25 import BM25Plus

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.common.interfaces import Citation

from .policy_ingest import Chunk

CHUNKS_FILE = "rag/policy_chunks.jsonl"
DEFAULT_CONFIDENTIALITY = frozenset({"public", "internal"})  # role mapping is deferred (D21)
RRF_K = 60  # standard reciprocal-rank-fusion constant

# a token is a whole reference such as "POL-COLL-004" or "§4.2", plus its parts, plus plain words
_REF = re.compile(r"[\w§]+(?:[-.][\w§]+)+")
_WORD = re.compile(r"\w+|§")

VectorFn = Callable[[str, list[Chunk]], dict[str, float]]  # chunk_id -> similarity
RerankFn = Callable[[str, list[Chunk]], list[Chunk]]


def tokenize(text: str) -> list[str]:
    """Lowercase tokens that keep references intact so `POL-COLL-004` and `§4.2` match exactly."""
    t = text.lower()
    return _REF.findall(t) + _WORD.findall(t)


def family(c: Chunk) -> str:
    """Documents that are versions of each other share a family (v4.1 points at v4.2 via superseded_by)."""
    return c.meta["superseded_by"] or c.doc_id


def in_force(chunks: list[Chunk], as_of: date) -> list[Chunk]:
    """Keep, per document family and language, the latest version effective on or before `as_of`.

    Filtering on an empty `superseded_by` would be right today but wrong for a past as-of date.
    """
    live = [c for c in chunks if date.fromisoformat(c.meta["effective_date"]) <= as_of]
    best: dict[tuple[str, str], str] = {}
    for c in live:
        key = (family(c), c.meta["language"])
        cur = best.get(key)
        if cur is None or c.meta["effective_date"] > cur:
            best[key] = c.meta["effective_date"]
    return [c for c in live if c.meta["effective_date"] == best[(family(c), c.meta["language"])]]


def translation_ids(chunks: list[Chunk]) -> set[str]:
    """IDs of French renderings of an English document (`POL-COLL-001-FR` next to `POL-COLL-001`).

    English is the reference: the French versions are condensed summaries that drop sections, so searching them
    would return less than the English document says. A French-only document, such as the Quebec annex
    `REG-SUM-001-QC`, has no English twin and is not a translation, so it stays searchable.
    """
    english = {c.doc_id for c in chunks if c.meta["language"] == "EN"}
    return {c.doc_id for c in chunks if c.meta["language"] != "EN" and c.doc_id.endswith("-FR")
            and c.doc_id[:-3] in english}


def _specific_to(c: Chunk, province: str) -> bool:
    """A chunk written for particular provinces (not "ALL") that include `province`."""
    provs = c.meta["applies_to_provinces"]
    return "ALL" not in provs and province in provs


def _applies(values: list[str], wanted: str | None) -> bool:
    return wanted is None or "ALL" in values or wanted in values


@dataclass
class Section:
    """One retrieved section, with everything a citation needs (D14)."""
    doc_id: str
    title: str
    version: str
    effective_date: str
    section_id: str
    section_title: str
    language: str
    text: str
    score: float
    doc_type: str = ""
    chunk_ids: list[str] = field(default_factory=list)
    pinned: bool = False  # a province-specific rule, included because the question is about that province

    def citation(self) -> Citation:
        return Citation("policy", self.doc_id, self.version, self.section_id)


@dataclass
class SearchResult:
    sections: list[Section]
    hardship_rows: list[dict]
    as_of_date: str
    candidates_considered: int  # how many chunks survived the filters


class PolicyIndex:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks

    @classmethod
    def load(cls, path=None) -> "PolicyIndex":
        p = path or config.warehouse_path().parent / CHUNKS_FILE
        with open(p, encoding="utf-8") as f:
            return cls([Chunk(**json.loads(line)) for line in f])

    # -- 1. filter -----------------------------------------------------------------------
    def eligible(self, as_of: date, confidentiality=DEFAULT_CONFIDENTIALITY, product: str | None = None,
                 province: str | None = None, doc_types: set[str] | None = None,
                 english_reference: bool = True) -> list[Chunk]:
        pool = [c for c in self.chunks if c.meta["confidentiality"] in confidentiality]
        if english_reference:
            skip = translation_ids(self.chunks)
            pool = [c for c in pool if c.doc_id not in skip]
        pool = in_force(pool, as_of)
        pool = [c for c in pool if _applies(c.meta["applies_to_products"], product)
                and _applies(c.meta["applies_to_provinces"], province)]
        if doc_types:
            pool = [c for c in pool if c.meta["doc_type"] in doc_types]
        return pool

    # -- 2-5. search, rerank, expand, attach -----------------------------------------------
    def search(self, query: str, as_of: date | None = None, *, language: str | None = None,
               product: str | None = None, province: str | None = None,
               confidentiality=DEFAULT_CONFIDENTIALITY, doc_types: set[str] | None = None,
               n_candidates: int = 20, top_k: int = 5, vector_fn: VectorFn | None = None,
               rerank_fn: RerankFn | None = None, with_hardship: bool = True,
               english_reference: bool = True) -> SearchResult:
        as_of = as_of or config.AS_OF_DATE
        pool = self.eligible(as_of, confidentiality, product, province, doc_types, english_reference)
        if not pool:
            return SearchResult([], [], as_of.isoformat(), 0)

        scores = self._keyword(query, pool)
        if vector_fn is not None:
            scores = self._fuse(scores, vector_fn(query, pool))
        ranked = sorted(pool, key=lambda c: scores.get(c.chunk_id, 0.0), reverse=True)
        cands = [c for c in ranked if scores.get(c.chunk_id, 0.0) > 0][:n_candidates]
        if rerank_fn is not None:
            cands = rerank_fn(query, cands)

        sections = self._expand(cands, scores, language)
        if province:  # a province-specific rule overrides the general one, so it must never be missed
            pinned = self._expand([c for c in pool if _specific_to(c, province)], scores, language)
            for sec in pinned:
                sec.pinned = True
            keys = {(x.doc_id, x.section_id) for x in pinned}
            sections = pinned + [x for x in sections if (x.doc_id, x.section_id) not in keys]
            sections = sections[:max(top_k, len(pinned))]
        else:
            sections = sections[:top_k]
        rows = self._hardship_rows(sections, as_of, query, product) if with_hardship else []
        return SearchResult(sections, rows, as_of.isoformat(), len(pool))

    @staticmethod
    def _keyword(query: str, pool: list[Chunk]) -> dict[str, float]:
        # the document ID is indexed too, so a query naming POL-COLL-004 finds it by exact reference
        bm25 = BM25Plus([tokenize(f"{c.doc_id} {c.text}") for c in pool])
        raw = bm25.get_scores(tokenize(query))
        return {c.chunk_id: float(s) for c, s in zip(pool, raw)}

    @staticmethod
    def _fuse(keyword: dict[str, float], vector: dict[str, float]) -> dict[str, float]:
        """Reciprocal rank fusion: robust to the two searches having different score scales."""
        out: dict[str, float] = {}
        for ranking in (keyword, vector):
            for rank, cid in enumerate(sorted(ranking, key=ranking.get, reverse=True), start=1):
                if ranking[cid] > 0:
                    out[cid] = out.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        return out

    @staticmethod
    def _expand(cands: list[Chunk], scores: dict[str, float], language: str | None) -> list[Section]:
        """Replace each hit by its parent section; one entry per (document family, section)."""
        best: dict[tuple[str, str], Section] = {}
        order: list[tuple[str, str]] = []
        for c in cands:
            key = (family(c), c.section_id)
            sec = Section(c.doc_id, c.meta["title"], c.meta["version"], c.meta["effective_date"],
                          c.section_id, c.section_title, c.meta["language"], c.section_text,
                          scores.get(c.chunk_id, 0.0), c.meta["doc_type"], [c.chunk_id])
            if key not in best:
                best[key] = sec
                order.append(key)
                continue
            cur = best[key]
            if sec.doc_id == cur.doc_id:  # another sub-chunk of the same section
                cur.chunk_ids.append(c.chunk_id)
                cur.score = max(cur.score, sec.score)
            elif language and sec.language == language and cur.language != language:
                best[key] = sec  # same section in another language: keep the user's
        return [best[k] for k in order]

    # -- 5. hardship programme rows (SQL, D5) --------------------------------------------
    @staticmethod
    def _hardship_rows(sections: list[Section], as_of: date, query: str, product: str | None,
                       max_rows: int = 3) -> list[dict]:
        """Programme rows linked to the retrieved sections, valid on the as-of date, best match first.

        `policy_section` points at a section of POL-COLL-004 (e.g. §4.2); HPT-2026 sections are
        keyed by programme ID. When the question names no programme, every linked row is returned
        so the answer step can choose; otherwise only the `max_rows` that match the question.
        """
        pol_sections = {s.section_id for s in sections if s.doc_id.startswith("POL-COLL-004")}
        programmes = {p.strip() for s in sections if s.doc_id == "HPT-2026" for p in s.section_id.split("/")}
        if not pol_sections and not programmes:
            return []
        con = connect(read_only=True)
        cur = con.execute(
            "SELECT * EXCLUDE (_ingested_at) FROM raw.hardship_programs "
            "WHERE effective_from <= ? AND ? <= effective_to ORDER BY program_id",
            [as_of.isoformat(), as_of.isoformat()])  # ISO strings compare correctly; 9999-12-31 = current
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        rows = [r for r in rows if r["policy_section"] in pol_sections or r["program_id"] in programmes]
        if product:
            rows = [r for r in rows if product in json.loads(r["product_types"] or "[]")]
        if len(rows) <= max_rows:
            return rows
        text = lambda r: f"{r['program_id']} {r['program_name']} {r['description']}"
        scores = BM25Plus([tokenize(text(r)) for r in rows]).get_scores(tokenize(query))
        ranked = sorted(zip(scores, rows), key=lambda x: x[0], reverse=True)
        hits = [r for sc, r in ranked if sc > 0][:max_rows]
        return hits or rows
