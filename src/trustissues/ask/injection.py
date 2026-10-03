"""Heuristic prompt-injection screen (fallback that needs no LLM). Flags, never rewrites, the question."""
from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS = [
    (r"\bfrom now on\b.{0,60}\b(?:unrestricted|no rules|no restrictions)\b", "role_override"),
    (r"\bpretend\b.{0,30}\b(?:admin|administrator|unrestricted)\b", "role_override"),
    (r"\byou are now\b|\bpretend (to be|you are)\b|\bact as (if|an? )", "role_override"),
    (r"\b(developer|debug|admin|god) mode\b|\bjailbreak\b|\bDAN\b", "mode_switch"),
    (r"\b(drop|truncate|alter|delete from|insert into|update \w+ set|attach|copy .* to)\b.{0,40}\b(table|database|from|into|set|'.*')", "sql_write"),
    (r"\b(read_csv|read_parquet|read_text|glob)\s*\(", "file_reader"),
    (r"\bas (an? )?(admin|administrator|team lead|analyst)\b.{0,30}\b(i|we) (am|are|have)\b|\bi am (the |an? )?(admin|team lead|analyst)\b", "role_claim"),
    (r"--\s*$|;\s*(select|drop|delete)\b", "sql_smuggling"),
]
_COMPILED = [(re.compile(p, re.I), name) for p, name in PATTERNS]

# Independent semantic slots: verb and object can appear in either order and
# can be separated by politeness, a heading, or intervening instructions.
_OVERRIDE = re.compile(r'\b(?:forget|disregard|ignore|override|bypass)\b', re.I)
_REVEAL = re.compile(r'\b(?:reveal|print|show|repeat|output)\b', re.I)
_WITHOUT = re.compile(r'\bact\s+without\b', re.I)
_OBJECT = re.compile(r'\b(?:instructions?|rules?|limits?|configuration|prompts?|guidelines?|'
                     r'restrictions?|safeguards?|polic(?:y|ies))\b|'
                     r'\b(?:everything|what)\s+you\s+(?:(?:were|have been|had been|are)\s+)?told\b', re.I)
_REVEAL_OBJECT = re.compile(r'\b(?:configuration|prompts?)\b|'
                            r'\b(?:your|system|hidden|initial|assistant)\s+'
                            r'(?:instructions?|rules?|limits?|restrictions?)\b|'
                            r'\bnew\s+instructions\b', re.I)


@dataclass(frozen=True)
class InjectionFlag:
    kind: str
    matched: str


def screen(question: str) -> InjectionFlag | None:
    for verb, objects, kind in ((_OVERRIDE, _OBJECT, 'override_instructions'),
                                (_WITHOUT, _OBJECT, 'role_override'),
                                (_REVEAL, _REVEAL_OBJECT, 'prompt_leak')):
        action, target = verb.search(question), objects.search(question)
        if action and target:
            start, end = min(action.start(), target.start()), max(action.end(), target.end())
            return InjectionFlag(kind, question[start:end])
    for rx, name in _COMPILED:
        m = rx.search(question)
        if m:
            return InjectionFlag(name, m.group(0))
    return None
