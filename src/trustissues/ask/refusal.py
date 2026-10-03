"""Refusal gate: runs before routing, so a refused question never reaches SQL or RAG."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from trustissues.common.config import CONFIG_DIR

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
    return [(cat, spec["reason"], [re.compile(p, re.I) for p in spec["patterns"]]) for cat, spec in doc.items()]


def check(question: str, path=LEXICON_PATH) -> Refusal | None:
    """First matching category, or None. Order in the YAML decides precedence."""
    for cat, reason, patterns in _rules(path):
        for p in patterns:
            m = p.search(question)
            if m:
                return Refusal(cat, reason, m.group(0))
    return None
