"""nba/fairness.py: group metrics, the screening flags, and that the report shows aggregates only."""
import re

import numpy as np
import pandas as pd
import pytest

from trustissues.common import config
from trustissues.nba import fairness


def _rows(n_per_group: dict[str, int], miscalibrated: str | None = None, seed: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scored rows for customers in groups; scores are calibrated except for one group, where they are 0.3 too high."""
    rng = np.random.default_rng(seed)
    rows, groups = [], {}
    for g, n in n_per_group.items():
        for i in range(n):
            cid = f"{g}-{i}"
            p = rng.uniform(0.1, 0.6)
            y = int(rng.random() < p)
            rows.append({"crm_customer_id": cid, "label": y, "ours": p + (0.3 if g == miscalibrated else 0.0), "bank": p})
            if g != "missing-from-crm":
                groups[cid] = g
    gdf = pd.DataFrame({"gender_code": list(groups.values())}, index=pd.Index(list(groups), name="crm_customer_id"))
    return pd.DataFrame(rows), gdf


def test_small_groups_are_left_out_and_unmatched_rows_are_labelled():
    df, groups = _rows({"F": 2000, "M": 2000, "X": fairness.MIN_GROUP - 1, "missing-from-crm": 500})
    t, overall = fairness.model_by_group(df, groups, "gender_code")
    assert set(t["group"]) == {"F", "M", fairness.NOT_IN_CRM}
    assert t.set_index("group").loc["F", "n"] == 2000
    assert overall["ours"]["auc"] > 0.5


def test_a_miscalibrated_group_is_flagged_and_only_for_that_score():
    df, groups = _rows({"F": 3000, "M": 3000}, miscalibrated="F")
    t, overall = fairness.model_by_group(df, groups, "gender_code")
    assert any(f.startswith("calibration gap") and "for F" in f for f in fairness.model_flags(t, overall, "ours"))
    assert not any(f.startswith("calibration") for f in fairness.model_flags(t, overall, "bank"))


def test_missing_and_unmatched_groups_are_shown_but_not_screened():
    df, groups = _rows({"F": 3000, "M": 3000, "missing-from-crm": 3000})
    df.loc[df["crm_customer_id"].str.startswith("missing"), "ours"] += 0.3  # badly scored, but not a protected group
    t, overall = fairness.model_by_group(df, groups, "gender_code")
    assert fairness.NOT_IN_CRM in set(t["group"])
    assert not any(fairness.NOT_IN_CRM in f for f in fairness.model_flags(t, overall, "ours"))


def test_report_holds_aggregates_only():
    """On the real data: no customer id reaches the report, and every protected attribute is covered."""
    from trustissues.common.db import connect
    from trustissues.features import sources

    path = config.warehouse_path()
    models = config.p3_dir() / "models"
    if not path.exists() or not (models / "case_nocure_v0.json").exists():
        pytest.skip("needs the warehouse and the trained models")
    con = connect(read_only=True)
    sources.register(con)
    text = fairness.build_report(con)
    ids = set(sources.protected_attributes(con).df()["crm_customer_id"].sample(2000, random_state=1))
    assert not ids & set(re.findall(r"\b[A-Z]{3}-\d{4}\b", text))
    for label in fairness.ATTRIBUTES.values():
        assert label in text
