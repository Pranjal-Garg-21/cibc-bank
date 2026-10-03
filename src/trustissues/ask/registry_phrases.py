"""Derive literal field phrases from sensitivity labels; no source rows are read."""
from __future__ import annotations

import re
from pathlib import Path

import yaml


def field_phrases(column: str) -> set[str]:
    """Keep the field's precise name as well as a readable representation.

    Do not turn fields like agent_display_name into a blanket ban on 'agent',
    'display', or 'name'. Operational words embedded in sensitive field names
    must not become separate refusal triggers.
    """
    phrases = {column.casefold(), column.replace('_', ' ').casefold()}
    words = column.split('_')
    while words and words[0] in {'std', 'raw'}:
        words.pop(0)
    while words and words[-1] in {'raw', 'std', 'code', 'flag', 'masked', 'hash'}:
        words.pop()
    if words:
        phrases.add(' '.join(words).casefold())
    return {re.sub(r'\s+', ' ', p).strip() for p in phrases if p.strip()}


def generate(path: Path, levels: set[str], *, proxies: bool = False) -> tuple[str, ...]:
    """New fields carrying a selected label automatically join the phrase index."""
    doc = yaml.safe_load(path.read_text()) or {}
    phrases = set()
    for fields in doc.get('tables', {}).values():
        for column, level in fields.items():
            if level in levels:
                phrases.update(field_phrases(column))
    for field in doc.get('protected_columns', []) if 'protected' in levels else []:
        phrases.update(field_phrases(field.split('.',1)[-1]))
    for field in doc.get('proxy_columns', []) if proxies else []:
        phrases.update(field_phrases(field.split('.',1)[-1]))
    return tuple(sorted(phrases))
