"""Load a saved break-risk model and score promises with it.

Scores are calibrated with the isotonic curve stored next to the model (linear interpolation between
its thresholds, the same as IsotonicRegression.predict), so serving needs no scikit-learn objects.
Contributions are TreeSHAP values on the log-odds of the uncalibrated model.
"""
from __future__ import annotations

import json
from functools import cached_property

import lightgbm as lgb
import numpy as np
import pandas as pd

from trustissues.common import config
from trustissues.nba.train import MODEL_NAME, prepare


class BreakModel:
    def __init__(self, name: str = MODEL_NAME):
        self.name = name
        base = config.p3_dir() / "models" / name
        self.meta = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
        self.booster = lgb.Booster(model_file=str(base.with_suffix(".txt")))

    @property
    def features(self) -> list[str]:
        return self.meta["features"]

    @cached_property
    def _curve(self) -> tuple[np.ndarray, np.ndarray]:
        cal = self.meta["calibration"]
        return np.asarray(cal["x"]), np.asarray(cal["y"])

    def frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return prepare(df, self.features, self.meta["categories"])

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """Calibrated probability that each promise breaks (or is only partly kept)."""
        raw = self.booster.predict(self.frame(df))
        x, y = self._curve
        return np.interp(raw, x, y)

    def contributions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-feature SHAP contribution (log-odds) for each row, columns in feature order."""
        contrib = self.booster.predict(self.frame(df), pred_contrib=True)[:, :-1]
        return pd.DataFrame(contrib, columns=self.features, index=df.index)
