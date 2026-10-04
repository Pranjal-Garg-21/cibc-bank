"""Turn backend return values (DataFrames, dataclasses, dates, numpy scalars) into plain JSON values."""
from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime, time
from decimal import Decimal


def jsonable(v):
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: jsonable(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [jsonable(x) for x in v]
    if hasattr(v, "to_dict") and hasattr(v, "columns"):  # pandas DataFrame
        return table(v)
    if hasattr(v, "item"):  # numpy scalar
        return jsonable(v.item())
    if hasattr(v, "isoformat"):  # pandas Timestamp
        return v.isoformat()
    try:
        import pandas as pd
        if v is pd.NaT or v is pd.NA:
            return None
    except ImportError:
        pass
    return str(v)


def table(df) -> dict:
    """A DataFrame as {columns, rows}: compact and keeps column order."""
    return {"columns": [str(c) for c in df.columns], "rows": [[jsonable(x) for x in r] for r in df.itertuples(index=False)]}
