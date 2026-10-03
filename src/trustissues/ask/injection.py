"""Heuristic prompt-injection screen (fallback that needs no LLM). Flags, never rewrites, the question."""
from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS = [
    (r"^\s*(?:forget|ignore|disregard|bypass|override)\s+(?:(?:all|any|the)\s+)?"
     r"(?:rules?|instructions?|guidelines?|restrictions?|safeguards?|polic(?:y|ies))\b", "override_instructions"),
    (r"\b(?:forget|ignore|disregard|bypass|override)\s+(?:(?:all|any|the)\s+)?"
     r"(?:(?:your|previous|prior|earlier|above|system|safety|security|existing)\s+){1,3}"
     r"(?:rules?|instructions?|guidelines?|restrictions?|safeguards?|polic(?:y|ies))\b", "override_instructions"),
    (r"\b(?:ignore|disregard)\b.{0,30}\b(?:what you (?:were|have been) told|your instructions)\b", "override_instructions"),
    (r"\bfrom now on\b.{0,60}\b(?:unrestricted|no rules|no restrictions)\b", "role_override"),
    (r"\bpretend\b.{0,30}\b(?:admin|administrator|unrestricted)\b", "role_override"),
    (r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)", "override_instructions"),
    (r"disregard (all |any |the )?(previous|prior|above|your) (instructions|rules|guidelines)", "override_instructions"),
    (r"(reveal|show|print|repeat|output) (me )?(your|the) (system|hidden|initial) (prompt|instructions?)", "prompt_leak"),
    (r"\byou are now\b|\bpretend (to be|you are)\b|\bact as (if|an? )", "role_override"),
    (r"\b(developer|debug|admin|god) mode\b|\bjailbreak\b|\bDAN\b", "mode_switch"),
    (r"\b(drop|truncate|alter|delete from|insert into|update \w+ set|attach|copy .* to)\b.{0,40}\b(table|database|from|into|set|'.*')", "sql_write"),
    (r"\b(read_csv|read_parquet|read_text|glob)\s*\(", "file_reader"),
    (r"\bas (an? )?(admin|administrator|team lead|analyst)\b.{0,30}\b(i|we) (am|are|have)\b|\bi am (the |an? )?(admin|team lead|analyst)\b", "role_claim"),
    (r"--\s*$|;\s*(select|drop|delete)\b", "sql_smuggling"),
]
_COMPILED = [(re.compile(p, re.I), name) for p, name in PATTERNS]


@dataclass(frozen=True)
class InjectionFlag:
    kind: str
    matched: str


def screen(question: str) -> InjectionFlag | None:
    for rx, name in _COMPILED:
        m = rx.search(question)
        if m:
            return InjectionFlag(name, m.group(0))
    return None
