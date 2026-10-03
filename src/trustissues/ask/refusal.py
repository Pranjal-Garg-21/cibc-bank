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


def _channel_mention(question: str, mention: re.Match) -> bool:
    before, after = question[:mention.start()], question[mention.end():]
    if re.match(r'\s+(?:numbers?|addresses?|details)\b', after, re.I):
        return False
    return bool(re.search(r'\b(?:by|via|using|through|over)\s+(?:the\s+)?$', before, re.I)
                or re.match(r'\s+(?:attempts?|calls?|contacts?|connections?|connected|rates?|counts?|'
                            r'volumes?|messages?|reminders?|notifications?|channels?|statistics|stats|totals)\b',
                            after, re.I)
                or re.match(r'\s+(?:[a-z]+[\s-]+){0,3}(?:rates?|counts?|volumes?|statistics|stats|totals)\b',
                            after, re.I))


_EXPORT_ACTION = re.compile(r'\b(?:export|send|list|download|give(?:\s+me)?|dump|show|extract|'
                            r'provide|print|output|display|reveal|fetch|return)\b', re.I)
_BULK_SCOPE = re.compile(r'\b(?:every|all|cohorts?|list)\b', re.I)
_CONTACT_DETAIL = re.compile(r'\b(?:e-?mails?|phones?|addresses|address|contact\s+details)\b', re.I)
_OPERATIONAL_NAME = re.compile(r'\b(?:product|metric|column|table|field|queue|channel|'
                               r'program|programme|strategy|site|file)\s+$', re.I)
_CONTACT_NOUN = re.compile(r'\s+(?:numbers?|details|addresses?|attempts?|calls?|contacts?|'
                           r'counts?|rates?|volumes?|costs?|logs?|statistics|stats)\b', re.I)


def _has_intent(question: str, category: str, patterns: list[re.Pattern]) -> bool:
    for pattern in patterns:
        for action in pattern.finditer(question):
            # "Phone anyone" is treatment; "phone numbers" is a detail noun.
            # Other targeting verbs in the same request are still inspected.
            if (category == 'name_origin_discrimination'
                    and action[0].casefold() in {'phone', 'phones', 'call', 'calls', 'contact', 'contacts'}
                    and _CONTACT_NOUN.match(question[action.end():])):
                continue
            return True
    return False


def _bulk_contact_export(question: str) -> str | None:
    """Order-independent action + detail + bulk scope; no adjective list required."""
    if not (_EXPORT_ACTION.search(question) and _BULK_SCOPE.search(question)):
        return None
    for mention in _CONTACT_DETAIL.finditer(question):
        if mention[0].casefold() in {'phone', 'phones', 'email', 'emails', 'e-mail', 'e-mails'}:
            if _channel_mention(question, mention):
                continue
        return mention[0]
    return None


def _channel_only(question: str, phrase: str) -> bool:
    """A bare channel field is ambiguous; inspect each mention, not the whole question.

    Exact compound PII identifiers remain sensitive. A mixed request containing
    channel statistics and actual addresses must not get a blanket exemption.
    """
    if phrase not in {'phone', 'email', 'e-mail'}:
        return False
    mentions = list(re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', question, re.I))
    return bool(mentions) and all(_channel_mention(question, mention) for mention in mentions)


def check(question: str, path=LEXICON_PATH) -> Refusal | None:
    """First matching category, or None. Order in the YAML decides precedence."""
    rules, index = _rules(path)
    literal_hits = index.hits(question)
    for cat, reason, patterns, intent_patterns in rules:
        # Apply the same intent condition to regexes and generated registry
        # phrases, so mentioning a PII field in a procedure is not disclosure.
        if intent_patterns and not _has_intent(question, cat, intent_patterns):
            continue
        if cat == 'pii_export':
            detail = _bulk_contact_export(question)
            if detail:
                return Refusal(cat, reason, detail)
        for p in patterns:
            for m in p.finditer(question):
                if cat == 'name_origin_discrimination' and _OPERATIONAL_NAME.search(question[:m.start()]):
                    continue
                return Refusal(cat, reason, m.group(0))
        for hit in literal_hits:
            if hit.category == cat:
                if cat == 'pii_export' and _channel_only(question, hit.phrase):
                    continue
                return Refusal(cat, reason, hit.phrase)
    return None
