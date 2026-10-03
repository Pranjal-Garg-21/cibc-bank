"""Detect whether a question is English or French, so the answer comes back in the asker's language (design D8).

Deterministic on purpose: it is free, gives the same result every time, and runs before any model call, which
the fixed "not found" text and the search both need. It counts common function words and French accents.
Only EN and FR are supported (the bank's two languages); anything unclear falls back to English.
"""
from __future__ import annotations

import re

# Function words that are clearly one language. Words that are valid in both ("a", "on", "or", "me") are left out.
_EN = frozenset("""the is are was were what how does do did can could must should would of to for in with when why who
which whose and not this that these those there their they customer's i you we my our your if it its be been have
has had will shall may any all from by at about after before between during""".split())
_FR = frozenset("""le la les un une des du de est sont était quel quelle quels quelles que qui quoi pour dans par sur
avec sans combien comment pourquoi quand peut peuvent doit doivent dois nous vous je tu il elle ils elles ce cette ces
au aux et ou ne pas n est-ce suis sommes êtes ont avons avez mon ma mes notre nos votre vos leur leurs si être avoir
après avant entre pendant""".split())
_ACCENT = re.compile(r"[àâçéèêëîïôùûüœ]")
_WORD = re.compile(r"[a-zàâçéèêëîïôùûüœ]+(?:-[a-zàâçéèêëîïôùûüœ]+)*")


def detect_language(text: str, default: str = "EN") -> str:
    """Return "EN" or "FR". Ties and empty text give `default`."""
    t = text.lower().replace("’", "'")
    words = _WORD.findall(t)
    # an elided word such as "l'heure" or "d'appels": the part before the apostrophe is French
    fr = sum(w in _FR for w in words) + len(re.findall(r"\b[ldjnsctm]'", t)) + len(_ACCENT.findall(t))
    en = sum(w in _EN for w in words)
    return "FR" if fr > en else "EN" if en > fr else default
