"""Mask PII before anything is shown. Default is to hide; a role must be allowed to see a column.

Levels come from `config/sensitivity.yaml` (generated from the data dictionary). Columns it does not know,
such as gold views or UI frames, are classified by name from `config/masking.yaml`; anything still unknown
and not clearly a key, date, flag or measure is masked (fail closed). Free text goes through P4's `scrub()`
(phone numbers, e-mail addresses, SIN-style numbers, card numbers, dates of birth, postal codes, long digit runs).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from trustissues.common.config import CONFIG_DIR
from trustissues.factory.curate.text_controls import scrub

SENSITIVITY_PATH = CONFIG_DIR / "sensitivity.yaml"
MASKING_PATH = CONFIG_DIR / "masking.yaml"
MASK = "***"
# Strictest first: when the same column name has different labels in different tables, the strictest wins.
STRICTNESS = ("protected", "direct_pii", "quasi_identifier", "unclassified", "none")


@dataclass(frozen=True)
class ColumnPolicy:
    column: str
    level: str
    action: str  # show | partial | mask | drop


@lru_cache(maxsize=4)
def _config(path: Path = MASKING_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    doc["_name_rules"] = [(lvl, [re.compile(p, re.I) for p in pats]) for lvl, pats in doc["name_rules"].items()]
    doc["_safe"] = [re.compile(p, re.I) for p in doc["safe_name_patterns"]]
    return doc


@lru_cache(maxsize=4)
def _sensitivity(path: Path = SENSITIVITY_PATH) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """-> (strictest level per column name, level per (table, column)). Proxy columns count as protected."""
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    by_name: dict[str, str] = {}
    by_table: dict[tuple[str, str], str] = {}

    def put(table, col, level):
        by_table[(table, col)] = level
        if col not in by_name or STRICTNESS.index(level) < STRICTNESS.index(by_name[col]):
            by_name[col] = level

    for table, cols in (doc.get("tables") or {}).items():
        for col, level in cols.items():
            put(table, col.lower(), level)
    for qualified in doc.get("proxy_columns", []):
        table, col = qualified.split(".", 1)
        put(table, col.lower(), "protected")
    for qualified in doc.get("unclassified_columns", []):
        table, col = qualified.split(".", 1)
        by_table.setdefault((table, col.lower()), "unclassified")
        by_name.setdefault(col.lower(), "unclassified")
    return by_name, by_table


def classify(column: str, table: str | None = None) -> str:
    """Sensitivity level of one column."""
    col = column.lower()
    by_name, by_table = _sensitivity()
    if table and (table, col) in by_table:
        return by_table[(table, col)]
    if col in by_name:
        return by_name[col]
    cfg = _config()
    for level, patterns in cfg["_name_rules"]:
        if any(p.search(col) for p in patterns):
            return level
    if any(p.search(col) for p in cfg["_safe"]):
        return "none"
    return "unclassified"


def policy_for(columns, role: str, table: str | None = None) -> list[ColumnPolicy]:
    roles = _config()["roles"]
    if role not in roles:
        raise ValueError(f"Unknown role: {role}")
    out = []
    for c in columns:
        level = classify(c, table)
        out.append(ColumnPolicy(c, level, roles[role][level]))
    return out


# ---- value masking ------------------------------------------------------------------------------------------

def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def partial(column: str, value) -> str:
    """Keep a recognisable fragment: enough to confirm a person on the phone, not to identify them."""
    if value is None:
        return value
    s, col = str(value), column.lower()
    if re.search(r"email|e_mail", col):
        user, _, domain = s.partition("@")
        return f"{user[:1]}{MASK}@{domain}" if domain else MASK
    if re.search(r"phone|mobile", col):
        d = _digits(s)
        return f"{MASK}-{MASK}-{d[-4:]}" if len(d) >= 4 else MASK
    if re.search(r"dob|birth", col):
        m = re.match(r"(\d{4})", s)
        return f"{m[1]}-**-**" if m else MASK
    if re.search(r"postal|zip|fsa", col):
        t = re.sub(r"\s", "", s)
        return f"{t[:3].upper()} {MASK}" if len(t) >= 3 else MASK
    if re.search(r"name", col):
        return " ".join(f"{w[:1]}{MASK}" for w in s.split()) or MASK
    d = _digits(s)
    return f"{MASK}{d[-4:]}" if len(d) >= 6 else MASK  # account / card / SIN style numbers: last 4 only


def mask_text(text: str | None, role: str) -> str | None:
    """Replace identifiers inside free text (notes, transcripts). Every role gets the same replacement:
    a note's context is shown, its identifiers never are. The replacement is P4's `scrub()`, the single scrubber
    (factory/curate/text_controls.py), so a note reads the same here as in the timeline and RAG."""
    if role not in _config()["roles"]:
        raise ValueError(f"Unknown role: {role}")
    return None if text is None else scrub(text).text


def _apply(action: str, column: str, value):
    if action == "show":
        return value
    if action == "partial":
        return partial(column, value)
    return None if value is None else MASK


def mask_rows(columns, rows, role: str, table: str | None = None):
    """-> (columns, rows) with dropped columns removed and the rest masked per role."""
    pol = policy_for(columns, role, table)
    keep = [i for i, p in enumerate(pol) if p.action != "drop"]
    return ([columns[i] for i in keep],
            [tuple(_apply(pol[i].action, columns[i], r[i]) for i in keep) for r in rows])


def mask_frame(df, role: str, table: str | None = None):
    """Same as mask_rows for a pandas DataFrame (returns a new frame; the input is not changed)."""
    cols, rows = mask_rows(list(df.columns), list(df.itertuples(index=False, name=None)), role, table)
    return type(df)(rows, columns=cols)


def mask_answer_card(card, role: str):
    """Mask a service AnswerCard's table for display. Returns a copy; the text answer is masked as free text."""
    import dataclasses

    cols, rows = mask_rows(list(card.columns), list(card.rows), role) if card.columns else ((), ())
    return dataclasses.replace(card, columns=tuple(cols), rows=tuple(rows), answer=mask_text(card.answer, role))
