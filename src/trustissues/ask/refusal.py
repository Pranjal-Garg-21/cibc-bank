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
        compiled.append((cat, spec['reason'], [re.compile(p, re.I) for p in spec['patterns']],
                         [re.compile(p, re.I) for p in spec.get('intent_patterns', [])]))
    return compiled, PhraseIndex(literals)


def _channel_only(question: str, phrase: str) -> bool:
    """A bare channel field is ambiguous; inspect each mention, not the whole question.

    Exact compound PII identifiers remain sensitive. A mixed request containing
    channel statistics and actual addresses must not get a blanket exemption.
    """
    if phrase not in {'phone', 'email', 'e-mail'}:
        return False
    mentions = list(re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', question, re.I))
    for mention in mentions:
        before, after = question[:mention.start()], question[mention.end():]
        channel = (re.search(r'\b(?:by|via|using|through|over)\s+(?:the\s+)?$', before, re.I)
                   or re.match(r'\s+(?:attempts?|calls?|contacts?|connections?|connected|rates?|counts?|'
                               r'volumes?|messages?|reminders?|notifications?|channels?)\b', after, re.I))
        if not channel:
            return False
    return bool(mentions)


def check(question: str, path=LEXICON_PATH) -> Refusal | None:
    """First matching category, or None. Order in the YAML decides precedence."""
    rules, index = _rules(path)
    literal_hits = index.hits(question)
    for cat, reason, patterns, intent_patterns in rules:
        # Apply the same intent condition to regexes and generated registry
        # phrases, so mentioning a PII field in a procedure is not disclosure.
        if intent_patterns and not any(p.search(question) for p in intent_patterns):
            continue
        for p in patterns:
            m = p.search(question)
            if m:
                return Refusal(cat, reason, m.group(0))
        for hit in literal_hits:
            if hit.category == cat:
                if cat == 'pii_export' and _channel_only(question, hit.phrase):
                    continue
                return Refusal(cat, reason, hit.phrase)
    return None
