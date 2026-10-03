"""The Feast registry serves the same feature values features/compute.py wrote, point in time.

Needs `python -m trustissues.features.compute` and `feast apply` (run in feature_repo/); skipped otherwise.
"""
import math

import pytest

feast = pytest.importorskip("feast")
pd = pytest.importorskip("pandas")

from trustissues.common import config  # noqa: E402
from trustissues.features.compute import GROUPS, VIEWS_DIR  # noqa: E402

N = 20


@pytest.fixture(scope="module")
def fs():
    try:
        s = feast.FeatureStore(repo_path=str(config.REPO_ROOT / "feature_repo"))
        views = {fv.name for fv in s.list_feature_views()}
    except Exception as e:  # registry missing, feature files not built, ...
        pytest.skip(f"feast not applied: {e}")
    if not set(GROUPS) <= views:
        pytest.skip("feast apply has not registered every feature view")
    return s


def _same(a, b):
    if a is None or (isinstance(a, float) and math.isnan(a)):
        return b is None or (isinstance(b, float) and math.isnan(b))
    if isinstance(a, float) or isinstance(b, float):
        return b is not None and math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    return a == b


@pytest.mark.parametrize("group", GROUPS)
def test_historical_retrieval_equals_computed_features(fs, group):
    df = pd.read_parquet(config.p3_dir() / VIEWS_DIR / f"{group}.parquet").sample(N, random_state=3)
    feats = [c for c in df.columns if c not in ("ptp_id", "golden_customer_id", "crm_customer_id", "event_ts")]
    entity_df = pd.DataFrame({"ptp_id": df["ptp_id"], "event_timestamp": pd.to_datetime(df["event_ts"])})
    got = fs.get_historical_features(entity_df=entity_df, features=[f"{group}:{c}" for c in feats]).to_df()
    got = got.set_index("ptp_id")
    diffs = []
    for _, row in df.iterrows():
        for f in feats:
            exp, off = row[f], got.loc[row["ptp_id"], f]
            exp = None if pd.isna(exp) else (exp.item() if hasattr(exp, "item") else exp)
            off = None if pd.isna(off) else (off.item() if hasattr(off, "item") else off)
            if not _same(exp, off):
                diffs.append(f"{row['ptp_id']} {f}: computed={exp} feast={off}")
    assert not diffs, "\n".join(diffs[:20])
