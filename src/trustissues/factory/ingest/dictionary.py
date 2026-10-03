"""Parse DATA_DICTIONARY.xlsx into raw._dictionary / raw._relationships and generate
config/sensitivity.yaml, the single source of truth for protected, PII and proxy columns.

Sensitivity is default-deny: any column that appears in the loaded data but is not classified
in the dictionary is written as `unclassified`, and the decisioning view only allow-lists
columns that are explicitly `none`. A new column in a new extract therefore never leaks into a
model by accident; it shows up in the report below until someone classifies it.
"""
from __future__ import annotations

import re
from collections import defaultdict

import openpyxl
import yaml

from trustissues.common import config
from trustissues.common.db import connect

# Columns that are not protected attributes themselves but can stand in for them.
PROXY_PATTERNS = [r"(^|_)age($|_)", r"accent", r"interpreter", r"dob_year", r"birth_year"]
# 'age' as a whole word only: ageing buckets, age_of_account style names are matched manually
PROXY_EXCLUDE = [r"ageing", r"aging", r"account_age", r"age_of", r"avg_age", r"trade_age", r"usage", r"manage", r"language", r"^dq_"]

SENS_MAP = {
    "Direct PII": "direct_pii",
    "Quasi-identifier": "quasi_identifier",
    "Protected attribute": "protected",
}


def _rows(wb, sheet):
    rows = list(wb[sheet].iter_rows(values_only=True))
    head = [str(h).strip() if h is not None else "" for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:] if any(v is not None for v in r)]


def is_proxy(column: str) -> bool:
    c = column.lower()
    if any(re.search(p, c) for p in PROXY_EXCLUDE):
        return False
    return any(re.search(p, c) for p in PROXY_PATTERNS)


def load_dictionary(con, xlsx_path) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    tables, columns, rels = _rows(wb, "Tables"), _rows(wb, "Columns"), _rows(wb, "Relationships")

    for name, rows in (("_dictionary_tables", tables), ("_dictionary", columns), ("_relationships", rels)):
        cols = list(rows[0].keys())
        con.execute(f"CREATE OR REPLACE TABLE raw.{name} ({', '.join(chr(34) + c + chr(34) + ' VARCHAR' for c in cols)})")
        con.executemany(
            f"INSERT INTO raw.{name} VALUES ({','.join('?' * len(cols))})",
            [[None if r[c] is None else str(r[c]) for c in cols] for r in rows],
        )
    return {"tables": len(tables), "columns": len(columns), "relationships": len(rels)}


def load_overrides() -> dict:
    path = config.CONFIG_DIR / "sensitivity_overrides.yaml"
    if not path.exists():
        return {}
    return (yaml.safe_load(open(path, encoding="utf-8")) or {}).get("overrides") or {}


def load_curate_cfg() -> dict:
    """config/curate.yaml, or {} when the curated layer is not configured (ingest works without it)."""
    path = config.CONFIG_DIR / "curate.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_dq_cfg() -> dict:
    path = config.CONFIG_DIR / "dq_checks.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def curated_columns(con) -> dict[str, list[str]]:
    """Columns of curated.* tables (empty before the curated build has run)."""
    out: dict[str, list[str]] = defaultdict(list)
    for t, c in con.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='curated' "
        "AND table_name NOT LIKE '!_%' ESCAPE '!' ORDER BY table_name, ordinal_position"
    ).fetchall():
        out[t].append(c)
    return out


def derive_labels(tables: dict, curated: dict, curate_cfg: dict, dq_cfg: dict | None = None) -> None:
    """Derived curated columns inherit the label of the column they come from, so a standardised
    DOB is as sensitive as the raw DOB. Adds <table>.<derived column> entries to `tables` in place."""
    ident = curate_cfg.get("identity_fields") or {}
    for t, cfg in ident.items():
        cols = tables.get(t, {})
        src = {}
        if cfg.get("name"):
            src["std_name_"] = cfg["name"]["col"]
        if cfg.get("dob"):
            src["std_dob"] = cfg["dob"]
        for label, col in (cfg.get("phones") or {}).items():
            src[f"std_phone_{label}_"] = col
        if cfg.get("postal"):
            src["std_postal"] = src["std_fsa"] = cfg["postal"]
        if cfg.get("postal_from_address"):
            src["std_postal"] = src["std_fsa"] = cfg["postal_from_address"]
        if cfg.get("employer"):
            src["std_employer"] = cfg["employer"]
        for c in curated.get(t, []):
            for prefix, base in src.items():
                if c.startswith("std_") and c.startswith(prefix):
                    cols[c] = cols.get(base, "unclassified")
                    break
    for d in (dq_cfg or {}).get("derived_columns", []):  # channel_resolved, cif10, salutation_std
        t = d["table"].split(".", 1)[1]
        tables.setdefault(t, {})[d["column"]] = tables.get(t, {}).get(d["from"], "unclassified")
    for t, cs in curated.items():  # dq_* flags describe data quality, not a person
        for c in cs:
            if c.startswith("dq_"):
                tables.setdefault(t, {})[c] = "none"
    for t, cs in curated.items():  # <col>__raw keeps the original text of a failed cast
        for c in cs:
            if c.endswith("__raw"):
                tables.setdefault(t, {})[c] = tables.get(t, {}).get(c[: -len("__raw")], "unclassified")


def build_sensitivity(con) -> dict:
    """Merge dictionary classification with the columns actually present in raw."""
    classified: dict[tuple[str, str], str] = {}
    for t, c, s in con.execute('SELECT "Table", "Column", "Sensitivity" FROM raw._dictionary').fetchall():
        classified[(t, c)] = SENS_MAP.get(s, "none") if s else "none"

    actual = con.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema='raw' AND table_name NOT LIKE '\\_%' ESCAPE '\\' AND column_name NOT LIKE '\\_%' ESCAPE '\\' "
        "ORDER BY table_name, ordinal_position"
    ).fetchall()

    tables: dict[str, dict[str, str]] = defaultdict(dict)
    for t, c in actual:
        tables[t][c] = classified.get((t, c), "unclassified")
    for (t, c), s in classified.items():  # in dictionary but not (yet) loaded
        tables[t].setdefault(c, s)

    overrides = load_overrides()
    for key, o in overrides.items():
        t, c = key.split(".", 1)
        if t in tables and c in tables[t]:
            tables[t][c] = o["label"]
    derive_labels(tables, curated_columns(con), load_curate_cfg(), load_dq_cfg())

    protected = sorted({f"{t}.{c}" for t, cs in tables.items() for c, s in cs.items() if s == "protected"})
    proxies = sorted({f"{t}.{c}" for t, cs in tables.items() for c, s in cs.items() if s == "none" and is_proxy(c)})
    unclassified = sorted({f"{t}.{c}" for t, cs in tables.items() for c, s in cs.items() if s == "unclassified"})
    return {
        "generated_from": "DATA_DICTIONARY.xlsx + config/sensitivity_overrides.yaml + raw and curated columns",
        "overrides_applied": sorted(overrides),
        "levels": ["direct_pii", "quasi_identifier", "protected", "none", "unclassified"],
        "protected_columns": protected,
        "proxy_columns": proxies,
        "unclassified_columns": unclassified,
        "tables": {t: dict(cs) for t, cs in sorted(tables.items())},
    }


def write_sensitivity(doc: dict) -> None:
    path = config.CONFIG_DIR / "sensitivity.yaml"
    with open(path, "w", encoding="utf-8") as f:
        f.write("# GENERATED by trustissues.factory.ingest.dictionary. Do not edit by hand.\n")
        yaml.safe_dump(doc, f, sort_keys=False, width=120)


def main() -> None:
    root = config.maple_data_dir()
    con = connect()
    stats = load_dictionary(con, root / "DATA_DICTIONARY.xlsx")
    doc = build_sensitivity(con)
    write_sensitivity(doc)
    con.close()
    counts: dict[str, int] = defaultdict(int)
    for cs in doc["tables"].values():
        for s in cs.values():
            counts[s] += 1
    print("dictionary:", stats)
    print("sensitivity:", dict(counts))
    print("protected:", len(doc["protected_columns"]), "| proxy candidates:", doc["proxy_columns"])
    if doc["unclassified_columns"]:
        print("UNCLASSIFIED (treated as restricted):", doc["unclassified_columns"][:20])


if __name__ == "__main__":
    main()
