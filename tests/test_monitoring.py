"""governance/monitoring.py: PSI, status levels, and the snapshot the Data Quality page reads."""
import numpy as np
import pandas as pd
import pytest

from trustissues.common import config
from trustissues.governance import monitoring


def test_psi_is_zero_for_the_same_population_and_large_for_a_shifted_one():
    rng = np.random.default_rng(1)
    ref = rng.normal(0, 1, 20000)
    assert monitoring.psi(ref, rng.normal(0, 1, 20000)) < 0.01
    assert monitoring.psi(ref, rng.normal(1, 1, 20000)) > monitoring.PSI_ACT
    assert monitoring.psi([], [1.0]) is None


def test_psi_handles_categories_and_missing_values():
    ref = pd.Series(["card"] * 50 + ["loan"] * 50, dtype=object)
    assert monitoring.psi(ref, ref.copy()) < 1e-9
    assert monitoring.psi(ref, pd.Series(["card"] * 95 + ["loan"] * 5, dtype=object)) > monitoring.PSI_ACT
    nums = pd.Series([1.0, 2.0, 3.0, None] * 100)
    assert monitoring.psi(nums, nums.copy()) < 1e-9
    assert monitoring.psi(nums, pd.Series([None] * 400, dtype=float)) > monitoring.PSI_ACT  # all missing now


def test_levels():
    assert [monitoring._level(v, 0.1, 0.25) for v in (0.05, 0.1, 0.3, None)] == ["ok", "watch", "act", "ok"]
    assert monitoring._worst(["ok", "act", "watch"]) == "act" and monitoring._worst([]) == "ok"


def test_snapshot_on_the_real_data():
    from trustissues.common.db import connect

    if not config.warehouse_path().exists() or not (config.p3_dir() / "models" / "case_nocure_v0.json").exists():
        pytest.skip("needs the warehouse and the trained models")
    s = monitoring.snapshot(connect(read_only=True))
    assert s["status"] in monitoring.LEVELS and {m["model"] for m in s["models"]} == set(monitoring.MODELS)
    for m in s["models"]:
        assert m["holdout"]["auc"] > 0.5 and m["periods"]
    assert {b["model"] for b in s["bank_models"]} >= {"ptp_break", "cure_30d"}
    text = monitoring.render(s)
    for heading in ("## Alerts", "## Queue and review", "## Other owners"):
        assert heading in text
