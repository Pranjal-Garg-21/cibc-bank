"""One-pass Aho-Corasick index for literal phrases, preserving word boundaries."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class PhraseHit:
    category: str
    phrase: str


class PhraseIndex:
    def __init__(self, phrases: Iterable[tuple[str, str]]):
        by_phrase = {}
        for category, phrase in phrases:
            phrase = phrase.casefold()
            if phrase:
                by_phrase.setdefault(phrase, set()).add(category)
        try:
            import ahocorasick
        except ImportError:
            # The opt-in dependencies must not disable refusal enforcement in a
            # base installation. Exact phrases still match with word boundaries.
            self.automaton = None
            self.fallback = [(re.compile(r'(?<!\w)' + re.escape(p) + r'(?!\w)'), p, tuple(sorted(c)))
                             for p, c in by_phrase.items()]
            self.empty = not by_phrase
            return
        self.automaton = ahocorasick.Automaton()
        for phrase, categories in by_phrase.items():
            self.automaton.add_word(phrase, (phrase, tuple(sorted(categories))))
        self.empty = not by_phrase
        if not self.empty:
            self.automaton.make_automaton()

    def hits(self, text: str) -> tuple[PhraseHit, ...]:
        text = text.casefold()
        if self.empty:
            return ()
        if self.automaton is None:
            return tuple(PhraseHit(category, phrase) for pattern, phrase, categories in self.fallback
                         if pattern.search(text) for category in categories)
        found = []
        def word(character):
            return character.isalnum() or character == '_'
        for end, (phrase, categories) in self.automaton.iter(text):
            start = end + 1 - len(phrase)
            if start > 0 and word(text[start-1]):
                continue
            if end + 1 < len(text) and word(text[end+1]):
                continue
            found.extend(PhraseHit(category, phrase) for category in categories)
        return tuple(found)
