"""Push the latest feature values to the Redis online store (Feast).

    python -m trustissues.features.compute        # writes the feature-view Parquet files
    cd feature_repo && feast apply && cd ..
    python -m trustissues.features.materialize    # loads the online store up to AS_OF_DATE

Feast's Redis store keeps a row unless the new event_ts is newer. After renaming or changing features
for the same promises, clear the online store first (`docker compose exec redis redis-cli FLUSHALL`),
or the changed columns read back as None.

Stretch (not built): re-push on a payment or new-promise event.
"""
from __future__ import annotations

from datetime import datetime, time

from trustissues.common import config

REPO = config.REPO_ROOT / "feature_repo"


def store():
    from feast import FeatureStore

    return FeatureStore(repo_path=str(REPO))


def main() -> int:
    fs = store()
    end = datetime.combine(config.AS_OF_DATE, time.max)
    start = datetime(2000, 1, 1)
    views = [fv.name for fv in fs.list_feature_views()]
    print(f"materializing {views} from {start:%Y-%m-%d} to {end:%Y-%m-%d %H:%M}")
    fs.materialize(start_date=start, end_date=end, feature_views=views)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
