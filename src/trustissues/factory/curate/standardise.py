"""Pure standardisation functions for identity fields (no DB, no I/O, unit-tested).

Each source system writes names, dates of birth, phones and postal codes in its own format.
These functions turn them into one comparable form WITHOUT inventing information:
  - an ambiguous day/month date keeps both readings
  - a masked phone keeps its visible digits and is never turned into a full number
  - a name keeps initials as initials
When a value cannot be parsed the function returns None fields, never a guess.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


# ---------- dates of birth ----------

def _valid(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_dob(raw: str | None) -> dict:
    """-> {dob_iso, dob_alt_iso, dob_ambiguous}.

    dob_iso is the reading we trust most; dob_alt_iso is the other reading when day and month
    could be swapped (both <= 12 and different). Matching must treat both as candidates.
    Conventions used to rank the readings: '/' is month/day (North American), '-' and '.' are day-month.
    """
    out = {"dob_iso": None, "dob_alt_iso": None, "dob_ambiguous": False}
    if raw is None or not str(raw).strip():
        return out
    s = str(raw).strip()

    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s) or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:  # year first: unambiguous
        out["dob_iso"] = _valid(int(m[1]), int(m[2]), int(m[3]))
        return out

    m = re.fullmatch(r"([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m[1].upper() in MONTHS:  # "Feb 23 2008"
        out["dob_iso"] = _valid(int(m[3]), MONTHS[m[1].upper()], int(m[2]))
        return out
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4})", s)
    if m and m[2].upper() in MONTHS:  # "23 Feb 2008"
        out["dob_iso"] = _valid(int(m[3]), MONTHS[m[2].upper()], int(m[1]))
        return out

    m = re.fullmatch(r"(\d{1,2})([-/.])(\d{1,2})\2(\d{4})", s)
    if m:
        a, sep, b, y = int(m[1]), m[2], int(m[3]), int(m[4])
        md = _valid(y, a, b)  # a = month, b = day
        dm = _valid(y, b, a)  # a = day, b = month
        if a > 12:
            out["dob_iso"] = dm
        elif b > 12:
            out["dob_iso"] = md
        elif a == b:
            out["dob_iso"] = md
        else:
            primary, alt = (md, dm) if sep == "/" else (dm, md)
            out["dob_iso"], out["dob_alt_iso"], out["dob_ambiguous"] = primary, alt, True
    return out


# ---------- phones ----------

def parse_phone(raw: str | None) -> dict:
    """-> {phone_e164, phone_masked, phone_prefix, phone_suffix}.

    A phone with masked digits (e.g. 450672xxx81) has no e164; we keep the visible prefix
    and suffix so it can still support (weak) matching. Phone alone never links records.
    """
    out = {"phone_e164": None, "phone_masked": False, "phone_prefix": None, "phone_suffix": None}
    if raw is None or not str(raw).strip():
        return out
    s = str(raw).strip()
    if re.search(r"[xX*#]", s):
        compact = re.sub(r"[^0-9xX*#]", "", s)
        digits_front = re.match(r"\d*", compact)[0]
        digits_back = re.search(r"\d*$", compact)[0]
        if compact[:1] == "1" and len(compact) == 11:
            compact = compact[1:]
            digits_front = digits_front[1:] if digits_front else ""
        out.update(phone_masked=True, phone_prefix=digits_front or None, phone_suffix=digits_back or None)
        return out
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "23456789" and digits[3] in "23456789":
        out["phone_e164"] = "+1" + digits
    return out


# ---------- names ----------

def strip_accents(s: str) -> str:
    s = s.replace("�", "")  # defensive only: this release has no replacement characters (checked on the real data)
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_SUFFIXES = {"JR", "SR", "II", "III", "IV", "PHD", "MD"}


def _tokens(s: str) -> list[str]:
    s = strip_accents(s).upper().replace(".", " ")
    s = re.sub(r"[^A-Z' \-,]", " ", s)
    return [t for t in re.split(r"\s+", s.strip()) if t]


def parse_name(raw: str | None, order: str = "auto", caps_default: str | None = None) -> dict:
    """-> {first, middle_initials, last, first_is_initial, name_key, name_tokens}.

    order: 'first_last' | 'last_first' | 'auto'.
      'Last, First M'   -> last_first (a comma always wins)
      'DYER D C'        -> last_first  (auto: a trailing run of single letters means initials)
      'Ball Tracy'      -> only if order says so; ALL-CAPS two-word names are ambiguous in auto mode
    caps_default: what to assume for an ALL-CAPS two-word name with no comma. It is a per-source
    setting (cards write 'JESSICA SMIRNOV', loans write 'KAPOOR CASEY'); None leaves it ambiguous.
    name_key is the sorted token set, so 'SMITH JOHN' and 'JOHN SMITH' share a key.
    """
    out = {"first": None, "middle_initials": None, "last": None, "first_is_initial": False,
           "name_key": None, "name_tokens": None}
    if raw is None or not str(raw).strip():
        return out
    text = str(raw).strip()
    comma = "," in text
    toks = [t.strip("-,'") for t in _tokens(text)]
    toks = [t for t in toks if t and t not in _SUFFIXES]
    if not toks:
        return out
    out["name_tokens"] = " ".join(toks)
    out["name_key"] = " ".join(sorted(toks))

    if len(toks) == 1:
        out["last"] = toks[0]
        return out

    if comma:
        order = "last_first"
    elif order == "auto":
        singles = 0
        for t in reversed(toks[1:]):
            if len(t) == 1:
                singles += 1
            else:
                break
        if singles and len(toks[0]) > 1:
            order = "last_first"  # LAST I I
        elif len(toks[0]) == 1 and len(toks) == 2:
            order = "first_last"  # J LANGLOIS
        elif text != text.upper():
            order = "first_last"  # mixed case: assume natural order
        else:
            order = caps_default  # ALL CAPS, no comma: only the source's convention can decide

    if order == "last_first":
        last, rest = toks[0], toks[1:]
    elif order == "first_last":
        last, rest = toks[-1], toks[:-1]
    else:
        out["last"] = None
        return out  # keep name_key only; the matcher compares token sets

    out["last"] = last
    if rest:
        out["first"] = rest[0]
        out["first_is_initial"] = len(rest[0]) == 1
        out["middle_initials"] = "".join(t[0] for t in rest[1:]) or None
    return out


# ---------- postal codes ----------

def parse_postal(raw: str | None) -> dict:
    """-> {postal, fsa}. Valid Canadian postal codes only: A1A1A1 (no space) and its first 3 chars."""
    out = {"postal": None, "fsa": None}
    if raw is None:
        return out
    p = re.sub(r"[\s\-]", "", str(raw).upper())
    if re.fullmatch(r"[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\d[ABCEGHJ-NPRSTV-Z]\d", p):
        out["postal"], out["fsa"] = p, p[:3]
    return out


# ---------- employer ----------

_LEGAL = r"\b(LTD|LIMITED|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LLC|LP|LTEE|LTEE?|ENR|SENC|SA)\b\.?"


def normalise_employer(raw: str | None) -> str | None:
    """Drop the '(City)' suffix and legal-form words, uppercase, no accents.
    'Wildrose Machining Corp.' and 'Wildrose Machining Limited' -> 'WILDROSE MACHINING'."""
    if raw is None or not str(raw).strip():
        return None
    s = re.sub(r"\([^)]*\)", " ", str(raw))
    s = strip_accents(s).upper()
    s = re.sub(r"\bLT\b", " ", s)  # a stray 'LT' left behind by a truncated 'Ltee'
    s = re.sub(_LEGAL, " ", s)
    s = re.sub(r"[^A-Z0-9& ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None
