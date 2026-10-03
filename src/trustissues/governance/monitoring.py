"""Monitoring for the Data Quality page: are the models still accurate, has what they score drifted, and how is the
queue being reviewed. No AI.

    python -m trustissues.governance.monitoring      # writes reports/monitoring.md

`snapshot(con)` returns the same content as a dict, for the app's Data Quality page (implementation_plan.md §8).

Models: ours (`ptp_break_v0`, `case_nocure_v0`), with the bank's (`ptp_break`, `cure_30d`) on the same rows.
- Realised performance by period, on outcomes known now (the holdout): AUC and calibration gap (mean score minus the
  actual rate), per month a promise was made / per case month-end.
- Drift: PSI of today's scores (the queue's feature snapshot, warehouse/p3/snapshots/FS-<date>-<model>.parquet)
  against a like-for-like reference, and of each model's top features by SHAP. Reference: the promises open on the
  first holdout day (made before p3.split_date, resolved after it), since today's rows are open promises and
  resolved promises differ by construction (a promise for the full amount is usually paid and closed fast); for
  the case model, the cases open at the holdout month-ends. Reading: below PSI_WATCH stable, PSI_WATCH to
  PSI_ACT watch, PSI_ACT or more act. PSI measures a change in who is being scored, not in accuracy.
- Status (worst of): score PSI; the latest period's AUC more than AUC_DROP_WATCH / AUC_DROP_ACT below the holdout's;
  its calibration gap beyond CAL_WATCH / CAL_ACT.
- The bank's own models: latest psi_last_month and the AUC it states (`auc_at_validation`) from model_scores.
Queue and review: action mix and approvals from today's queue; override rate and pending approvals from
gold.nba_overrides (nba/review.py) once decisions are recorded.
Other owners: open identity reviews (gold.identity_review_queue, P1) and text-feature accuracy
(reports/text_feature_eval.md, P4) appear once they exist; until then the page says so.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features import sources

REPORT = config.REPO_ROOT / "reports" / "monitoring.md"
PSI_WATCH, PSI_ACT = 0.10, 0.25
AUC_DROP_WATCH, AUC_DROP_ACT = 0.03, 0.05
CAL_WATCH, CAL_ACT = 0.05, 0.10
TOP_FEATURES = 8
MIN_PERIOD = 500  # rows a period needs before its AUC and calibration are judged
LEVELS = ("ok", "watch", "act")


def _level(value: float | None, watch: float, act: float) -> str:
    if value is None or np.isnan(value):
        return "ok"
    return "act" if value >= act else "watch" if value >= watch else "ok"


def _worst(levels) -> str:
    return max(levels, key=LEVELS.index, default="ok")


def psi(reference, current, bins: int = 10) -> float | None:
    """Population stability index of `current` against `reference`. Numbers: deciles of the reference (missing
    values are their own bin). Text: categories. 0 = same distribution."""
    ref, cur = pd.Series(reference), pd.Series(current)
    if len(ref) == 0 or len(cur) == 0:
        return None
    if ref.dtype == object or isinstance(ref.dtype, pd.CategoricalDtype):
        r, c = ref.astype(str).value_counts(normalize=True), cur.astype(str).value_counts(normalize=True)
    else:
        ref, cur = pd.to_numeric(ref, errors="coerce"), pd.to_numeric(cur, errors="coerce")
        edges = np.unique(np.nanquantile(ref.dropna(), np.linspace(0, 1, bins + 1))) if ref.notna().any() else []
        def bucket(s):
            b = pd.Series(np.searchsorted(edges[1:-1], s, side="right"), index=s.index).astype(str)
            return b.where(s.notna(), "missing").value_counts(normalize=True)
        r, c = bucket(ref), bucket(cur)
    keys = r.index.union(c.index)
    r, c = r.reindex(keys, fill_value=0) + 1e-4, c.reindex(keys, fill_value=0) + 1e-4
    return float(((c - r) * np.log(c / r)).sum())


# ---------------------------------------------------------------- models

MODELS = {  # our model -> (bank's model, feature file, period column, key to the bank's score)
    "ptp_break_v0": ("ptp_break", "features.parquet", "event_ts", "ptp_id"),
    "case_nocure_v0": ("cure_30d", "case_features.parquet", "as_of_date", "row_id"),
}


def _bank_scores(con, name: str) -> dict:
    from trustissues.nba import evaluate, train

    if name == "ptp_break_v0":
        _, rows = evaluate.incumbent_holdout_scores(con)
        return {r[0]: r[2] for r in rows}
    return train.incumbent_cure_risk(con)


def model_health(con, name: str) -> dict:
    from trustissues.nba.model import BreakModel

    bank_name, file, period_col, key = MODELS[name]
    model = BreakModel(name)
    meta = model.meta
    ho = pd.read_parquet(config.p3_dir() / file).query("split == 'holdout'").reset_index(drop=True)
    ho["ours"] = model.predict(ho)
    ho["bank"] = ho[key].map(_bank_scores(con, name))
    ho["period"] = pd.to_datetime(ho[period_col]).dt.to_period("M").astype(str)
    ref = ho[pd.to_datetime(ho[period_col]) < pd.Timestamp(config.settings()["p3"]["split_date"])] \
        if name == "ptp_break_v0" else ho
    holdout_auc = float(roc_auc_score(ho["label"], ho["ours"]))

    periods = []
    for per, x in ho.groupby("period"):
        both = x.dropna(subset=["bank"])
        row = {"period": per, "n": len(x), "actual": float(x["label"].mean()),
               "auc": float(roc_auc_score(x["label"], x["ours"])) if x["label"].nunique() == 2 else None,
               "gap": float(x["ours"].mean() - x["label"].mean()),
               "bank_auc": float(roc_auc_score(both["label"], both["bank"])) if both["label"].nunique() == 2 else None,
               "bank_gap": float(both["bank"].mean() - both["label"].mean()) if len(both) else None}
        periods.append(row)
    judged = [p for p in periods if p["n"] >= MIN_PERIOD]
    latest = judged[-1] if judged else None

    snap_path = config.p3_dir() / "snapshots" / f"FS-{config.AS_OF_DATE:%Y%m%d}-{name}.parquet"
    score_psi, features = None, []
    current_n = 0
    if snap_path.exists():
        cur = pd.read_parquet(snap_path)
        current_n = len(cur)
        score_psi = psi(ref["ours"], cur["risk_score"])
        shap = meta.get("shap_mean_abs", {})
        for f in sorted(shap, key=lambda k: -shap[k])[:TOP_FEATURES]:
            if f in cur.columns and f in ho.columns:
                v = psi(ref[f], cur[f])
                features.append({"feature": f, "psi": v, "level": _level(v, PSI_WATCH, PSI_ACT)})

    levels = {
        "score_drift": _level(score_psi, PSI_WATCH, PSI_ACT),
        "accuracy": _level(holdout_auc - latest["auc"], AUC_DROP_WATCH, AUC_DROP_ACT) if latest and latest["auc"] else "ok",
        "calibration": _level(abs(latest["gap"]), CAL_WATCH, CAL_ACT) if latest else "ok",
    }
    return {
        "model": name, "bank_model": bank_name, "trained_rows": meta.get("rows", {}).get("train"),
        "holdout": {"rows": len(ho), "auc": holdout_auc, "gap": float(ho["ours"].mean() - ho["label"].mean())},
        "periods": periods, "latest_period": latest["period"] if latest else None,
        "current_rows": current_n, "reference_rows": len(ref), "score_psi": score_psi,
        "mean_score": {"reference": float(ref["ours"].mean()), "today": float(cur["risk_score"].mean())}
        if current_n else None,
        "features": features,
        "levels": levels, "status": _worst(levels.values()),
    }


def bank_models(con) -> list[dict]:
    """The bank's own models on their latest scoring day. The bank records PSI per row, with up to ~60 different
    values on the same day for one model (only ptp_break has one), so the day's median is used, with its highest."""
    rows = con.execute(f"""
        WITH h AS ({sources.sql("bank_model_health")}),
        last AS (SELECT model_name, max(score_date) AS d FROM h GROUP BY 1)
        SELECT h.model_name, any_value(h.model_version), last.d, median(h.psi_last_month), max(h.psi_last_month),
               median(h.auc_at_validation)
        FROM h JOIN last ON h.model_name = last.model_name AND h.score_date = last.d
        GROUP BY h.model_name, last.d ORDER BY 1""").fetchall()
    return [{"model": m, "version": v, "last_scored": d, "psi": p, "psi_max": pm, "stated_auc": a,
             "level": _level(p, PSI_WATCH, PSI_ACT)} for m, v, d, p, pm, a in rows]


# ---------------------------------------------------------------- queue, review, other owners

def _table_exists(con, schema: str, table: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                            [schema, table]).fetchone()[0])


def queue_health() -> dict | None:
    path = config.p3_dir() / "nba_decisions.parquet"
    if not path.exists():
        return None
    d = pd.read_parquet(path, columns=["action", "requires_approval", "approval_reason", "agent_id", "as_of_date"])
    return {"as_of": str(d["as_of_date"].max()), "cases": len(d),
            "actions": d["action"].value_counts().to_dict(),
            "approvals": d.loc[d["requires_approval"], "approval_reason"].value_counts().to_dict()}


def review_health(con) -> dict | None:
    """Override rate and pending approvals over the last 7 days, once gold.nba_overrides holds decisions."""
    from trustissues.nba import review

    if not (_table_exists(con, "gold", "nba_overrides") and _table_exists(con, "gold", "nba_decisions")):
        return None
    if not con.execute("SELECT count(*) FROM gold.nba_overrides").fetchone()[0]:
        return None
    end = config.AS_OF_DATE
    return review.summary(con, end - timedelta(days=6), end)


def identity_reviews(con) -> int | None:
    if not _table_exists(con, "gold", "identity_review_queue"):
        return None
    return con.execute("SELECT count(*) FROM gold.identity_review_queue").fetchone()[0]


def text_feature_eval() -> str | None:
    path = config.REPO_ROOT / "reports" / "text_feature_eval.md"
    return path.relative_to(config.REPO_ROOT).as_posix() if path.exists() else None


def snapshot(con) -> dict:
    """Everything the Data Quality page shows, as plain data."""
    sources.register(con)
    models = [model_health(con, m) for m in MODELS]
    bank = bank_models(con)
    alerts = []
    for m in models:
        for check, lvl in m["levels"].items():
            if lvl != "ok":
                alerts.append(f"{m['model']}: {check.replace('_', ' ')} is {lvl}")
        for f in m["features"]:
            if f["level"] == "act":
                alerts.append(f"{m['model']}: feature {f['feature']} drifted (PSI {f['psi']:.2f})")
    for b in bank:
        if b["level"] != "ok":
            alerts.append(f"bank's {b['model']}: PSI {b['psi']:.2f} ({b['level']})")
    return {"as_of": str(config.AS_OF_DATE), "models": models, "bank_models": bank, "queue": queue_health(),
            "review": review_health(con), "identity_reviews": identity_reviews(con),
            "text_feature_eval": text_feature_eval(), "alerts": alerts,
            "status": _worst([m["status"] for m in models])}


# ---------------------------------------------------------------- report

def _f(v, d=3, sign=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def render(s: dict) -> str:
    L: list[str] = []
    w = L.append
    w("# Monitoring")
    w("")
    w(f"Generated by `python -m trustissues.governance.monitoring` for {s['as_of']}. The same content feeds the Data "
      "Quality page (`snapshot()`). Status reads: **ok**, **watch** (look at it), **act** (retrain, recalibrate or "
      f"investigate). PSI below {PSI_WATCH} is stable, {PSI_WATCH}–{PSI_ACT} watch, {PSI_ACT}+ act; an AUC "
      f"{AUC_DROP_WATCH} / {AUC_DROP_ACT} below the holdout's, or a calibration gap beyond {CAL_WATCH} / {CAL_ACT}, "
      f"is watch / act. Periods need {MIN_PERIOD} rows to be judged.")
    w("")
    w(f"**Overall: {s['status']}.**")
    w("")
    w("## Alerts")
    w("")
    w("\n".join(f"- {a}" for a in s["alerts"]) if s["alerts"] else "- None.")
    for m in s["models"]:
        w("")
        w(f"## Model `{m['model']}` (the bank's equivalent: `{m['bank_model']}`): **{m['status']}**")
        w("")
        lv = m["levels"]
        w(f"Holdout {m['holdout']['rows']:,} rows: AUC {m['holdout']['auc']:.3f}, calibration gap "
          f"{m['holdout']['gap']:+.3f}. Today: {m['current_rows']:,} rows scored, mean score "
          f"{_f((m['mean_score'] or {}).get('today'))} against {_f((m['mean_score'] or {}).get('reference'))} in the "
          f"reference ({m['reference_rows']:,} rows); score PSI {_f(m['score_psi'])} ({lv['score_drift']}). Latest judged period {m['latest_period']}: accuracy "
          f"{lv['accuracy']}, calibration {lv['calibration']}.")
        w("")
        w("| Period | Rows | Actual | AUC ours | AUC bank | Gap ours | Gap bank |")
        w("|---|---|---|---|---|---|---|")
        for p in m["periods"]:
            w(f"| {p['period']} | {p['n']:,} | {p['actual']:.1%} | {_f(p['auc'])} | {_f(p['bank_auc'])} | "
              f"{_f(p['gap'], sign=True)} | {_f(p['bank_gap'], sign=True)} |")
        if m["features"]:
            w("")
            w("Top features by SHAP, today against the reference:")
            w("")
            w("| Feature | PSI | Status |")
            w("|---|---|---|")
            for f in m["features"]:
                w(f"| `{f['feature']}` | {_f(f['psi'])} | {f['level']} |")
    w("")
    w("## The bank's models (`model_scores`)")
    w("")
    w("What the bank itself records: the AUC it states at validation and the monthly PSI on its latest scoring day "
      "(the median of that day's rows: the bank writes PSI per row, with up to about 60 different values on one day).")
    w("")
    w("| Model | Version | Last scored | Stated AUC | PSI (median that day) | Highest that day | Status |")
    w("|---|---|---|---|---|---|---|")
    for b in s["bank_models"]:
        w(f"| `{b['model']}` | {b['version']} | {b['last_scored']} | {_f(b['stated_auc'], 2)} | {_f(b['psi'])} | "
          f"{_f(b['psi_max'])} | {b['level']} |")
    w("")
    w("## Queue and review")
    w("")
    q = s["queue"]
    if q:
        w(f"Queue for {q['as_of']}: {q['cases']:,} cases. Actions: "
          + ", ".join(f"{a} {n:,}" for a, n in q["actions"].items()) + ". Needing approval: "
          + ", ".join(f"{a} {n:,}" for a, n in q["approvals"].items()) + ".")
    else:
        w("No queue yet: run `python -m trustissues.nba.queue`.")
    w("")
    r = s["review"]
    if r:
        rate = "n/a" if r["override_rate"] is None else f"{r['override_rate']:.1%}"
        w(f"Last 7 days: {r['agent_decisions']:,} agent decisions, override rate {rate}; overrides by reason "
          f"{r['overrides_by_reason'] or 'none'}; approvals still pending {r['still_pending'] or 'none'}.")
    else:
        w("No review decisions recorded yet (`gold.nba_overrides` is empty or not built): override rate not available.")
    w("")
    w("## Other owners")
    w("")
    ir = s["identity_reviews"]
    w(f"- Open identity reviews (P1): {ir:,}." if ir is not None else
      "- Open identity reviews (P1): not available yet (`gold.identity_review_queue` not built).")
    te = s["text_feature_eval"]
    w(f"- Text-feature accuracy (P4): see `{te}`." if te else
      "- Text-feature accuracy (P4): not available yet (`reports/text_feature_eval.md` not written).")
    w("")
    return "\n".join(L)


def main() -> int:
    con = connect(read_only=True)
    s = snapshot(con)
    REPORT.write_text(render(s), encoding="utf-8")
    print(f"status {s['status']}; alerts:")
    for a in s["alerts"]:
        print(f"  {a}")
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
