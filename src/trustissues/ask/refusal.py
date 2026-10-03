"""Refusal gate: runs before routing, so a refused question never reaches SQL or RAG."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from trustissues.common.config import CONFIG_DIR
from .registry_phrases import generate
from .phrase_index import PhraseIndex

LEXICON_PATH = CONFIG_DIR / "refusal_lexicon.yaml"


@dataclass(frozen=True)
class Refusal:
    category: str
    reason: str
    matched: str


@lru_cache(maxsize=1)
def _rules(path=LEXICON_PATH):
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    compiled = []
    literals = []
    for cat, spec in doc.items():
        phrases = generate(CONFIG_DIR / 'sensitivity.yaml', set(spec.get('registry_levels', [])),
                           proxies=spec.get('registry_proxies', False)) if (
                               spec.get('registry_levels') or spec.get('registry_proxies')) else ()
        literals.extend((cat, p) for p in (*phrases, *spec.get('phrases', [])))
        compiled.append((cat, spec['reason'], [re.compile(p, re.I) for p in spec['patterns']]))
    return compiled, PhraseIndex(literals)


def check(question: str, path=LEXICON_PATH) -> Refusal | None:
    """First matching category, or None. Order in the YAML decides precedence."""
    rules, index = _rules(path)
    literal_hits = index.hits(question)
    for cat, reason, patterns in rules:
        for p in patterns:
            m = p.search(question)
            if m:
                return Refusal(cat, reason, m.group(0))
        # Generated direct-PII fields are refused for bulk disclosure intent;
        # their mention alone must not block policy or assigned-case questions.
        if cat == 'pii_export' and not re.search(r'\b(export|download|list|show|give|extract)\b', question, re.I):
            continue
        for hit in literal_hits:
            if hit.category == cat:
                return Refusal(cat, reason, hit.phrase)
    return None
