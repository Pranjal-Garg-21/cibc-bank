"""Models (v0): LightGBM on the point-in-time feature sets.

    python -m trustissues.nba.train          # promise break risk (ptp_break_v0) vs the bank's ptp_break
    python -m trustissues.nba.train --case   # case not curing in 30 days (case_nocure_v0) vs the bank's cure_30d

Promise model:
- trains on split=train, early-stops on split=valid, then fits isotonic calibration on valid
- scores the holdout with nba/evaluate.evaluate and writes the result next to the incumbent in
  reports/model_evaluation.md
- SHAP check: mean |SHAP| per feature on the holdout (LightGBM's built-in TreeSHAP), to catch a
  feature that looks too good to be true
- saves warehouse/p3/models/ptp_break_v0.txt and ptp_break_v0.json (features in order, categories,
  calibration, metrics)
"""
from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features.compute import CASE_CATEGORICAL, CASE_FEATURES_FILE, CATEGORICAL, FEATURES_FILE
from trustissues.nba import evaluate

MODEL_NAME = "ptp_break_v0"
CASE_MODEL_NAME = "case_nocure_v0"
NON_FEATURES = {"ptp_id", "crm_customer_id", "event_ts", "label", "split", "row_id", "case_id", "as_of_date"}
PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.03,
    "num_leaves": 63,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": 7,
    "verbose": -1,
}
SUSPICIOUS_AUC = 0.95
# Ablation: everything derived from the promised amount. ptp_to_overdue_ratio dominates v0; this shows
# how much of the lift depends on it (see the v0 section of the report).
AMOUNT_FEATURES = ["ptp_to_overdue_ratio", "ptp_amount", "min_due_to_ptp_amount", "ptp_amount_to_salary"]


def load(file: str = FEATURES_FILE) -> tuple[pd.DataFrame, list[str]]:
    df = pd.read_parquet(config.p3_dir() / file)
    features = [c for c in df.columns if c not in NON_FEATURES]
    return df, features


def prepare(df: pd.DataFrame, features: list[str], categories: dict[str, list[str]]) -> pd.DataFrame:
    X = df[features].copy()
    for c, cats in categories.items():
        X[c] = pd.Categorical(X[c], categories=cats)
    for c in X.columns:
        if c not in categories:
            X[c] = pd.to_numeric(X[c], errors="coerce").astype("float64")
    return X


def fit(Xtr, ytr, Xva, yva) -> tuple[lgb.Booster, IsotonicRegression]:
    """LightGBM early-stopped on valid, then isotonic calibration fitted on valid."""
    cats = [c for c in Xtr.columns if isinstance(Xtr[c].dtype, pd.CategoricalDtype)]
    booster = lgb.train(
        PARAMS,
        lgb.Dataset(Xtr, ytr, categorical_feature=cats),
        num_boost_round=5000,
        valid_sets=[lgb.Dataset(Xva, yva, categorical_feature=cats)],
        callbacks=[lgb.early_stopping(200, verbose=False), lgb.log_evaluation(0)],
    )
    raw_va = booster.predict(Xva, num_iteration=booster.best_iteration)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw_va, yva)
    return booster, iso


def shap_importance(booster: lgb.Booster, X: pd.DataFrame, n: int = 5000) -> list[tuple[str, float]]:
    sample = X.sample(min(n, len(X)), random_state=7)
    contrib = booster.predict(sample, pred_contrib=True)[:, :-1]  # last column is the expected value
    imp = np.abs(contrib).mean(axis=0)
    return sorted(zip(X.columns, imp.tolist()), key=lambda t: -t[1])


def main() -> int:
    df, features = load()
    train, valid, holdout = (df[df.split == s] for s in ("train", "valid", "holdout"))
    categories = {c: sorted(train[c].dropna().unique().tolist()) for c in CATEGORICAL}
    Xtr, Xva, Xho = (prepare(d, features, categories) for d in (train, valid, holdout))
    print(f"train {len(Xtr):,}  valid {len(Xva):,}  holdout {len(Xho):,}  features {len(features)}")

    booster, iso = fit(Xtr, train.label, Xva, valid.label)
    print(f"best iteration {booster.best_iteration}, valid AUC {booster.best_score['valid_0']['auc']:.4f}")
    raw_ho = booster.predict(Xho, num_iteration=booster.best_iteration)
    cal_ho = iso.predict(raw_ho)

    keep = [c for c in features if c not in AMOUNT_FEATURES]
    ab_booster, ab_iso = fit(Xtr[keep], train.label, Xva[keep], valid.label)
    ab_ho = ab_iso.predict(ab_booster.predict(Xho[keep], num_iteration=ab_booster.best_iteration))

    res_raw = evaluate.evaluate(raw_ho, holdout.label)
    res_cal = evaluate.evaluate(cal_ho, holdout.label)
    con = connect(read_only=True)
    _, inc_rows = evaluate.incumbent_holdout_scores(con)
    inc = dict((r[0], r[2]) for r in inc_rows)
    mask = holdout.ptp_id.map(inc.__contains__).to_numpy()
    res_inc = evaluate.evaluate(holdout.ptp_id[mask].map(inc), holdout.label[mask])
    res_v0_same = evaluate.evaluate(cal_ho[mask], holdout.label[mask])
    res_ablation = evaluate.evaluate(ab_ho[mask], holdout.label[mask])
    shap = shap_importance(booster, Xho)

    for name, r in (("incumbent", res_inc), ("v0 raw", res_raw), ("v0 calibrated", res_cal),
                    ("v0 no amounts", res_ablation)):
        print(f"{name:14} AUC {r['auc']:.4f}  Brier {r['brier']:.4f}  ECE {r['ece']:.4f}  "
              f"top-20% capture {r['top20_capture']:.1%}")
    print("top features by mean |SHAP|:", ", ".join(f"{n} {v:.3f}" for n, v in shap[:10]))
    if res_cal["auc"] > SUSPICIOUS_AUC:
        print(f"WARNING: holdout AUC above {SUSPICIOUS_AUC}: check the top SHAP features for leakage")

    out = config.p3_dir() / "models"
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / f"{MODEL_NAME}.txt"), num_iteration=booster.best_iteration)
    meta = {
        "model": MODEL_NAME,
        "features": features,
        "categorical": CATEGORICAL,
        "categories": categories,
        "best_iteration": booster.best_iteration,
        "params": PARAMS,
        "calibration": {"type": "isotonic", "x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()},
        "split": config.settings()["p3"],
        "rows": {"train": len(Xtr), "valid": len(Xva), "holdout": len(Xho)},
        "holdout": {k: v for k, v in res_cal.items() if k != "reliability"},
        "holdout_without_amount_features": {k: v for k, v in res_ablation.items() if k != "reliability"},
        "shap_mean_abs": dict(shap),
    }
    (out / f"{MODEL_NAME}.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"saved {out / MODEL_NAME}.txt and .json")

    shap_rows = "\n".join(f"| {i + 1} | `{n}` | {v:.4f} |" for i, (n, v) in enumerate(shap[:20]))
    evaluate.write_section("v0", f"""
## v0 LightGBM vs the incumbent

Trained on {len(Xtr):,} promises created before {config.settings()['p3']['valid_start']}, early-stopped
(best iteration {booster.best_iteration}) and isotonic-calibrated on the {len(Xva):,} promises created
from then until {config.settings()['p3']['split_date']}. {len(features)} features from `features/sql/`.
Same {res_inc['n']:,} holdout promises for both models.

{evaluate.comparison_table({"Incumbent ptp_break": res_inc, "v0 (calibrated)": res_v0_same, "v0 (raw)": evaluate.evaluate(raw_ho[mask], holdout.label[mask]), "v0 without amount features": res_ablation})}

The riskiest 20% can hold at most 20% / {res_cal['broken_rate']:.1%} = {min(1, 0.2 / res_cal['broken_rate']):.1%} of
broken promises at this broken rate, so compare top-20% capture against that ceiling.

**Leakage check on `ptp_to_overdue_ratio`** (it dominates the SHAP ranking below). It is
`ptp_amount / overdue_at_creation`, both recorded when the promise is made. Promises for the full
overdue amount break far less often. We checked it is not back-filled from the outcome:
`overdue_at_creation` matches the creating contact's `overdue_at_contact` in ~91% of promises of every
status, broken promises also carry ratio = 1, and the share of full-amount promises falls smoothly
across *fully resolved* creation months (about 49% through Apr 2026, then 38%, 27%, 20%, 15% for
May to Aug 2026), which outcome back-filling could not do. It is real drift: fewer full-amount
promises, and the broken rate rises with it. The "without amount features" column drops
{", ".join(f"`{c}`" for c in AMOUNT_FEATURES)} to show how much of the lift depends on them.

Calibration matters here: the broken rate is {res_cal['broken_rate']:.1%} on the holdout, against
{train.label.mean():.1%} in train and {valid.label.mean():.1%} in valid, so raw scores sit too low.

{evaluate.format_result("v0 (calibrated)", res_cal)}

### SHAP check (mean |SHAP| on a holdout sample, top 20)

| Rank | Feature | Mean abs SHAP (log-odds) |
|---|---|---|
{shap_rows}
""")
    print(f"wrote {evaluate.REPORT}")
    return 0


def save(name: str, booster, iso, features, categories, extra: dict) -> None:
    out = config.p3_dir() / "models"
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / f"{name}.txt"), num_iteration=booster.best_iteration)
    meta = {"model": name, "features": features, "categorical": list(categories), "categories": categories,
            "best_iteration": booster.best_iteration, "params": PARAMS,
            "calibration": {"type": "isotonic", "x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()},
            **extra}
    (out / f"{name}.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"saved {out / name}.txt and .json")


def incumbent_cure_risk(con) -> dict[str, float]:
    """row_id (case_id@month-end) -> 1 - the bank's cure_30d score on that date (its risk of not curing)."""
    cm = config.settings()["p3"]["case_model"]
    rows = con.execute(f"""
        SELECT case_id || '@' || strftime(score_date, '%Y-%m-%d'), 1 - arg_min(score, score_ts)
        FROM curated.model_scores WHERE model_name = '{cm["incumbent_model"]}' GROUP BY case_id, score_date""").fetchall()
    return dict(rows)


def main_case() -> int:
    df, features = load(CASE_FEATURES_FILE)
    train, valid, holdout = (df[df.split == s] for s in ("train", "valid", "holdout"))
    categories = {c: sorted(train[c].dropna().unique().tolist()) for c in CASE_CATEGORICAL}
    Xtr, Xva, Xho = (prepare(d, features, categories) for d in (train, valid, holdout))
    print(f"train {len(Xtr):,}  valid {len(Xva):,}  holdout {len(Xho):,}  features {len(features)}")

    booster, iso = fit(Xtr, train.label, Xva, valid.label)
    print(f"best iteration {booster.best_iteration}, valid AUC {booster.best_score['valid_0']['auc']:.4f}")
    cal_ho = iso.predict(booster.predict(Xho, num_iteration=booster.best_iteration))

    con = connect(read_only=True)
    inc = incumbent_cure_risk(con)
    mask = holdout.row_id.map(inc.__contains__).to_numpy()
    res_inc = evaluate.evaluate(holdout.row_id[mask].map(inc), holdout.label[mask])
    res_same = evaluate.evaluate(cal_ho[mask], holdout.label[mask])
    res_all = evaluate.evaluate(cal_ho, holdout.label)
    shap = shap_importance(booster, Xho)
    for name, r in (("incumbent cure_30d", res_inc), ("v0 (same cases)", res_same), ("v0 (all holdout)", res_all)):
        print(f"{name:20} AUC {r['auc']:.4f}  Brier {r['brier']:.4f}  ECE {r['ece']:.4f}  "
              f"top-20% capture {r['top20_capture']:.1%}")
    print("top features by mean |SHAP|:", ", ".join(f"{n} {v:.3f}" for n, v in shap[:10]))

    save(CASE_MODEL_NAME, booster, iso, features, categories, {
        "label": "1 = case not cured within horizon_days of the month-end",
        "split": config.settings()["p3"]["case_model"],
        "rows": {"train": len(Xtr), "valid": len(Xva), "holdout": len(Xho)},
        "holdout": {k: v for k, v in res_all.items() if k != "reliability"},
        "shap_mean_abs": dict(shap),
    })
    cm = config.settings()["p3"]["case_model"]
    shap_rows = "\n".join(f"| {i + 1} | `{n}` | {v:.4f} |" for i, (n, v) in enumerate(shap[:20]))
    evaluate.write_section("case_v0", f"""
## Case model v0: will an open case cure within {cm['horizon_days']} days? (vs the bank's `cure_30d`)

Every collections case open at a month-end is a row; label 1 = it was **not** paid up and closed within
{cm['horizon_days']} days. This gives every open case a risk score, whether or not it has an open promise
(the promise model only covers cases with one). Trained on {len(Xtr):,} rows with month-ends before
{cm['valid_start']}, early-stopped and isotonic-calibrated on {len(Xva):,} rows up to {cm['holdout_start']},
tested on the {len(Xho):,} rows of the month-ends the bank scored ({cm['holdout_start']} onwards).
{len(features)} features: the case so far (`case_state.sql`) plus the promise history, arrears, liquidity,
payday and contact-response groups shared with the promise model. Same {res_inc['n']:,} case-month rows
for both models; the bank's score is turned into a risk as 1 - `cure_30d`.

{evaluate.comparison_table({"Incumbent cure_30d": res_inc, "Case v0 (calibrated)": res_same})}

The not-cured rate drifts upward: {train.label.mean():.1%} in train, {valid.label.mean():.1%} in valid,
{holdout.label.mean():.1%} in the holdout, so calibration is fitted on the most recent months.

{evaluate.format_result("Case v0 (calibrated)", res_all)}

### SHAP check (mean |SHAP| on a holdout sample, top 20)

| Rank | Feature | Mean abs SHAP (log-odds) |
|---|---|---|
{shap_rows}
""")
    print(f"wrote {evaluate.REPORT}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main_case() if "--case" in sys.argv[1:] else main())
