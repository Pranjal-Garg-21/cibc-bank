"""Small-group warnings for tabular Ask answers; presentation only, never a query gate."""
from __future__ import annotations

from decimal import Decimal
from math import isfinite
from numbers import Real

import yaml

from trustissues.common.config import CONFIG_DIR


COUNT_COLUMNS = frozenset({"denominator", "cases", "n", "count", "calls", "promises", "accounts", "disputes"})


def _numeric(value):
    return isinstance(value, (Real, Decimal)) and not isinstance(value, bool)


def find_small_groups(columns, rows, *, min_group=5, count_metric=False):
    """Return (row index, group label) pairs; rate numerators alone are not group sizes.

    Only rectangular results with multiple rows and leading labels are checked.
    A numerator may be used when the caller explicitly identifies a count metric.
    """
    if (not isinstance(columns, (tuple, list)) or not isinstance(rows, (tuple, list))
            or len(rows) < 2 or not columns
            or any(not isinstance(row, (tuple, list)) or len(row) != len(columns) for row in rows)):
        return []
    names = [str(c).lower() for c in columns]
    counts = [i for i, name in enumerate(names) if name in COUNT_COLUMNS
              or name.endswith(("_count", "_cases", "_calls", "_promises", "_accounts"))
              or (count_metric and name == "numerator")]
    if not counts:
        return []
    first_numeric = next((i for i in range(len(columns)) if any(_numeric(row[i]) for row in rows)), len(columns))
    if first_numeric == 0 or first_numeric == len(columns):
        return []
    return [(i, " / ".join(str(v) if v is not None else "(missing)" for v in row[:first_numeric]))
            for i, row in enumerate(rows)
            if any(_numeric(row[c]) and isfinite(row[c]) and 0 <= row[c] < min_group for c in counts)]


def apply(card, *, format_rows):
    """Apply the configured note/suppression, leaving the answer intact on any failure."""
    if card.refused or card.path not in {"metric", "drivers", "sql"}:
        return card
    try:
        config = yaml.safe_load((CONFIG_DIR / "disclosure.yaml").read_text(encoding="utf-8"))
        minimum, mode = config["min_group"], config["mode"]
        if type(minimum) is not int or minimum < 1 or mode not in {"note", "suppress"}:
            return card
        small = find_small_groups(card.columns, card.rows, min_group=minimum,
                                  count_metric=card.metric_id == "dispute_count")
        if not small:
            return card
        labels = ", ".join(dict.fromkeys(label for _, label in small))
        note = f"Groups with fewer than {minimum} records ({labels}): small groups can identify individuals."
        rows, answer = card.rows, card.answer
        if mode == "suppress":
            indices = {i for i, _ in small}
            rows = tuple(tuple(None if i in indices and _numeric(v) else v for v in row)
                         for i, row in enumerate(card.rows))
            table = format_rows(card.columns, rows)
            answer = card.answer.partition("\n")[0] + "\n" + table if card.path == "drivers" else table
            note += " Numeric cells in these groups have been suppressed (null)."
        # Prepare every replacement before mutating so a formatting/config failure cannot damage an answer.
        notes = [*card.notes, note]
        card.rows, card.answer, card.notes = rows, answer, notes
    except Exception:
        pass
    return card
