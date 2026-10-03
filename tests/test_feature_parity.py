"""Offline (training) and online (serving) feature values match for the same promise and timestamp.

Needs `feast apply` + `python -m trustissues.features.materialize` and Redis running (docker compose
up -d redis); skipped otherwise.
"""
import math

import pytest

feast = pytest.importorskip("feast")
pd = pytest.importorskip("pandas")

from trustissues.common import config  # noqa: E402
from trustissues.features.compute import GROUPS, VIEWS_DIR  # noqa: E402
from trustissues.features.materialize import store  # noqa: E402

N = 20


@pytest.fixture(scope="module")
def fs():
    try:
        s = store()
        views = {fv.name for fv in s.list_feature_views()}
    except Exception as e:  # registry missing, Redis down, ...
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
def test_offline_equals_online(fs, group):
    df = pd.read_parquet(config.p3_dir() / VIEWS_DIR / f"{group}.parquet").sample(N, random_state=3)
    feats = [c for c in df.columns if c not in ("ptp_id", "crm_customer_id", "event_ts")]
    refs = [f"{group}:{c}" for c in feats]
    entity_df = pd.DataFrame({"ptp_id": df["ptp_id"], "event_timestamp": pd.to_datetime(df["event_ts"])})
    try:
        offline = fs.get_historical_features(entity_df=entity_df, features=refs).to_df().set_index("ptp_id")
        online = fs.get_online_features(features=refs, entity_rows=[{"ptp_id": p} for p in df["ptp_id"]]).to_dict()
    except Exception as e:
        pytest.skip(f"store not reachable: {e}")
    if all(v is None for v in online[feats[0]]):
        pytest.skip("online store is empty: run python -m trustissues.features.materialize")
    diffs = []
    for i, ptp_id in enumerate(online["ptp_id"]):
        for f in feats:
            off, on = offline.loc[ptp_id, f], online[f][i]
            off = None if pd.isna(off) else (off.item() if hasattr(off, "item") else off)
            if not _same(off, on):
                diffs.append(f"{ptp_id} {f}: offline={off} online={on}")
    assert not diffs, "\n".join(diffs[:20])
