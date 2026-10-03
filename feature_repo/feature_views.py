"""One Feast feature view per SQL file in src/trustissues/features/sql/.

Sources are the Parquet files features/compute.py writes (warehouse/p3/feature_views/<group>.parquet),
so each feature is defined once, in SQL. The schema is read from the Parquet file, not repeated here.
"""
from datetime import timedelta

import pyarrow.parquet as pq
from feast import FeatureView, Field, FileSource
from feast.types import Float64, Int64, String

from entities import promise
from trustissues.common import config
from trustissues.features.compute import GROUPS, VIEWS_DIR

KEYS = {"ptp_id", "crm_customer_id", "event_ts"}
TTL = timedelta(days=3650)  # promise features never expire for a promise


def _dtype(arrow_type):
    if arrow_type in ("string", "large_string"):
        return String
    if arrow_type.startswith(("int", "uint")) or arrow_type == "bool":
        return Int64
    return Float64


def _view(group: str) -> FeatureView:
    path = config.p3_dir() / VIEWS_DIR / f"{group}.parquet"
    schema = pq.read_schema(path)
    fields = [Field(name=f.name, dtype=_dtype(str(f.type))) for f in schema if f.name not in KEYS]
    return FeatureView(
        name=group,
        entities=[promise],
        ttl=TTL,
        schema=fields,
        online=True,
        source=FileSource(name=f"{group}_source", path=str(path), timestamp_field="event_ts"),
        tags={"sql": f"src/trustissues/features/sql/{group}.sql"},
    )


feature_views = [_view(g) for g in GROUPS]
globals().update({fv.name: fv for fv in feature_views})
