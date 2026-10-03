"""Fairness checks: outcomes compared across protected groups. Testing only (POL-COLL-004 §4.6).

    python -m trustissues.nba.fairness     # writes reports/fairness_report.md

Protected attributes (config/sensitivity.yaml) are never model features and never reach a rule or a decision.
They are joined back here, and only here, by crm_customer_id (`sources.protected_attributes`), to check:

1. The models, ours and the bank's on the same holdout rows (promise break risk vs `ptp_break`, case risk vs
   `cure_30d`). Within each group: is the score as accurate (AUC) and as honest (calibration: mean score minus
   the actual rate) as overall? Who lands in the riskiest 20% (selection rate), and what share of each group's
   bad outcomes does that catch (true positive rate)?
2. The morning NBA queue: does each group get collections contact, calls, support calls and waits at similar
   rates? Groups differ in risk, so contact is also compared with what the group's risk alone predicts
   (observed / expected, expected = the overall contact rate of each risk decile).
3. Proxy audit: can the case model's features predict the attribute (a small model, held-out AUC)? If they can,
   a feature may stand in for the attribute.

Screening thresholds (a screen for review, not a legal test): calibration gap more than CALIBRATION_TOLERANCE
away from overall; group AUC more than AUC_TOLERANCE below overall; selection or true positive rate ratio
(lowest / highest group) below FOUR_FIFTHS; contact observed / expected outside [FOUR_FIFTHS, 1 / FOUR_FIFTHS];
proxy AUC at least PROXY_AUC_FLAG. A model flag also needs the difference to be clear of noise: Z or more standard
errors (two-proportion test for the ratios, the group's sampling error for calibration), and an AUC flag needs
MIN_AUC_CLASS rows of each outcome in the group. Without this, small groups (a few hundred rows) flagged and
unflagged at random between two retrains of the same model. Only aggregates are written; groups under MIN_GROUP
people are left out.
Vulnerability is not compared: it is a safeguard that deliberately changes treatment (nba/hardship.py).
"""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from trustissues.common import config
from trustissues.common.db import connect
from trustissues.features import sources

REPORT = config.REPO_ROOT / "reports" / "fairness_report.md"
MIN_GROUP = 100
TOP_SHARE = 0.20
CALIBRATION_TOLERANCE = 0.05
AUC_TOLERANCE = 0.05
FOUR_FIFTHS = 0.8
PROXY_AUC_FLAG = 0.60
Z = 1.96  # a flagged difference must be at least this many standard errors (5% two-sided)
MIN_AUC_CLASS = 100  # rows of each outcome a group needs before its AUC can be flagged
NOT_RECORDED = "not recorded"
NOT_IN_CRM = "not in CRM"  # the case points at a CRM customer that does not exist (DQ check R1): not a protected group
COLLECTIONS = ("agent_call", "sms_reminder", "email_reminder", "push_reminder", "letter")

ATTRIBUTES = {
    "gender_code": "Gender",
    "age_band": "Age band",
    "marital_status": "Marital status",
    "citizenship_status": "Citizenship / residency",
    "newcomer_program_flag": "Newcomer programme",
    "accessibility_needs_flag": "Accessibility needs",
    "dependants": "Dependants",
    "preferred_language": "Preferred language (not protected; checked as a stand-in for origin)",
}
# attribute -> (what the proxy model tries to tell apart, positive groups, negative groups or None = everyone else)
PROXY_TARGETS = {
    "gender_code": ("female vs male", {"F"}, {"M"}),
    "age_band": ("65 or older vs younger", {"65+"}, None),
    "marital_status": ("married or common law vs not", {"married", "common_law"}, None),
    "citizenship_status": ("not a citizen vs citizen", {"permanent_resident", "work_permit", "study_permit"},
                           {"citizen"}),
    "newcomer_program_flag": ("in the newcomer programme", {"yes"}, {"no"}),
    "accessibility_needs_flag": ("has accessibility needs", {"yes"}, {"no"}),
    "dependants": ("has dependants", {"1", "2", "3+"}, {"0"}),
    "preferred_language": ("prefers a language other than English or French", {"ZH", "PA", "AR", "TL", "ES"},
                           {"EN", "FR"}),
}


# ---------------------------------------------------------------- data

def load_groups(con) -> pd.DataFrame:
    """crm_customer_id -> each attribute as a group label (missing = 'not recorded')."""
    df = sources.protected_attributes(con).df()
    out = pd.DataFrame(index=df["crm_customer_id"])
    for c in ("gender_code", "age_band", "marital_status", "citizenship_status", "preferred_language"):
        out[c] = df[c].fillna(NOT_RECORDED).to_numpy()
    for c in ("newcomer_program_flag", "accessibility_needs_flag"):
        out[c] = df[c].map({True: "yes", False: "no"}).fillna(NOT_RECORDED).to_numpy()
    out["dependants"] = [NOT_RECORDED if pd.isna(v) else ("3+" if v >= 3 else str(int(v)))
                         for v in df["dependants_count"]]
    return out


def model_rows(con) -> dict[str, pd.DataFrame]:
    """Holdout rows with both scores: crm_customer_id, label, ours, bank (risk of the bad outcome)."""
    from trustissues.features.compute import CASE_FEATURES_FILE, FEATURES_FILE
    from trustissues.nba import evaluate, train
    from trustissues.nba.model import BreakModel

    promises = pd.read_parquet(config.p3_dir() / FEATURES_FILE).query("split == 'holdout'").reset_index(drop=True)
    _, rows = evaluate.incumbent_holdout_scores(con)
    bank = {r[0]: r[2] for r in rows}
    promises = promises.assign(ours=BreakModel().predict(promises), bank=promises["ptp_id"].map(bank))

    cases = pd.read_parquet(config.p3_dir() / CASE_FEATURES_FILE).query("split == 'holdout'").reset_index(drop=True)
    bank_case = train.incumbent_cure_risk(con)
    cases = cases.assign(ours=BreakModel(train.CASE_MODEL_NAME).predict(cases), bank=cases["row_id"].map(bank_case))
    keep = ["crm_customer_id", "label", "ours", "bank"]
    return {
        "Promise break risk (ours `ptp_break_v0` vs the bank's `ptp_break`)": promises.dropna(subset=["bank"])[keep],
        "Case risk of not curing in 30 days (ours `case_nocure_v0` vs the bank's 1 - `cure_30d`)":
            cases.dropna(subset=["bank"])[keep],
    }


# ---------------------------------------------------------------- 1. models

def _auc(y, s) -> float | None:
    return float(roc_auc_score(y, s)) if 0 < y.sum() < len(y) else None


def model_by_group(df: pd.DataFrame, groups: pd.DataFrame, attr: str) -> tuple[pd.DataFrame, dict]:
    """Per group metrics for both scores, and the overall values they are compared with."""
    d = df.join(groups[[attr]], on="crm_customer_id")
    d[attr] = d[attr].fillna(NOT_IN_CRM)
    overall = {}
    for m in ("ours", "bank"):
        cut = d[m].quantile(1 - TOP_SHARE)
        d[f"sel_{m}"] = (d[m] >= cut).astype(int)
        overall[m] = {"auc": _auc(d["label"], d[m]), "cal": d[m].mean() - d["label"].mean()}
    rows = []
    for g, x in d.groupby(attr):
        if len(x) < MIN_GROUP:
            continue
        r = {"group": g, "n": len(x), "actual": x["label"].mean(), "pos": int(x["label"].sum())}
        for m in ("ours", "bank"):
            pos = x[x["label"] == 1]
            r |= {f"mean_{m}": x[m].mean(), f"cal_{m}": x[m].mean() - x["label"].mean(),
                  f"auc_{m}": _auc(x["label"], x[m]), f"sel_{m}": x[f"sel_{m}"].mean(),
                  f"tpr_{m}": pos[f"sel_{m}"].mean() if len(pos) else None}
        rows.append(r)
    return pd.DataFrame(rows).sort_values("n", ascending=False), overall


def _clear(p1: float, n1: int, p2: float, n2: int) -> bool:
    """Two-proportion z-test: is the difference at least Z standard errors?"""
    p = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return se > 0 and abs(p1 - p2) / se >= Z


def model_flags(t: pd.DataFrame, overall: dict, m: str) -> list[str]:
    """Screening flags across the recorded groups (missing attribute and not-in-CRM rows are shown, not screened)."""
    t = t[~t["group"].isin((NOT_IN_CRM, NOT_RECORDED))].set_index("group")
    flags = []
    cal = (t[f"cal_{m}"] - overall[m]["cal"]).abs()
    se = np.sqrt(t["actual"] * (1 - t["actual"]) / t["n"])
    cal = cal[(cal > CALIBRATION_TOLERANCE) & (cal > Z * se)]
    if len(cal):
        g = cal.idxmax()
        flags.append(f"calibration gap {t.loc[g, f'cal_{m}']:+.3f} for {g} (n={t.loc[g, 'n']:,}; overall "
                     f"{overall[m]['cal']:+.3f})")
    sized = (t["pos"] >= MIN_AUC_CLASS) & (t["n"] - t["pos"] >= MIN_AUC_CLASS)
    aucs = t.loc[sized, f"auc_{m}"].dropna()
    if len(aucs) and overall[m]["auc"] - aucs.min() > AUC_TOLERANCE:
        g = aucs.idxmin()
        flags.append(f"AUC {aucs.min():.3f} for {g} (n={t.loc[g, 'n']:,}; overall {overall[m]['auc']:.3f})")
    for k, name, size in (("sel", "riskiest-20% rate", t["n"]), ("tpr", "bad outcomes caught", t["pos"])):
        v = t[f"{k}_{m}"].dropna()
        if len(v) > 1 and v.max() > 0 and v.min() / v.max() < FOUR_FIFTHS \
                and _clear(v.min(), size[v.idxmin()], v.max(), size[v.idxmax()]):
            lo, hi = v.idxmin(), v.idxmax()
            flags.append(f"{name} {v.min():.1%} for {lo} (actual {t.loc[lo, 'actual']:.1%}) vs {v.max():.1%} for {hi} "
                         f"(actual {t.loc[hi, 'actual']:.1%}), ratio {v.min() / v.max():.2f}")
    return flags


# ---------------------------------------------------------------- 2. decisions

def decisions_by_group(groups: pd.DataFrame, attr: str) -> pd.DataFrame:
    d = pd.read_parquet(config.p3_dir() / "nba_decisions.parquet",
                        columns=["golden_customer_id", "action", "risk_score", "decision_card"])
    d["flagged"] = d["decision_card"].str.contains('Hardship or vulnerability:', regex=False)
    d["contact"] = d["action"].isin(COLLECTIONS).astype(int)
    d["decile"] = pd.qcut(d["risk_score"].rank(method="first"), 10, labels=False)
    plain = ~d["flagged"]
    rate = d[plain].groupby("decile")["contact"].mean()
    d["expected"] = d["decile"].map(rate)
    d = d.join(groups[[attr]], on="golden_customer_id")
    d[attr] = d[attr].fillna(NOT_IN_CRM)
    rows = []
    for g, x in d.groupby(attr):
        if len(x) < MIN_GROUP:
            continue
        p = x[~x["flagged"]]
        rows.append({
            "group": g, "cases": len(x), "risk": x["risk_score"].mean(),
            "collections": x["contact"].mean(), "calls": (x["action"] == "agent_call").mean(),
            "support": (x["action"] == "support_call").mean(), "wait": (x["action"] == "wait").mean(),
            "flagged": x["flagged"].mean(),
            "obs_exp": p["contact"].mean() / p["expected"].mean() if len(p) and p["expected"].mean() else None,
        })
    return pd.DataFrame(rows).sort_values("cases", ascending=False)


# ---------------------------------------------------------------- 3. proxy audit

def proxy_audit(groups: pd.DataFrame, sample: int = 60000) -> list[dict]:
    """How well the case model's own features predict each attribute (held-out AUC), and the features that do it."""
    from trustissues.features.compute import CASE_FEATURES_FILE
    from trustissues.nba import train
    from trustissues.nba.model import BreakModel

    model = BreakModel(train.CASE_MODEL_NAME)
    df = pd.read_parquet(config.p3_dir() / CASE_FEATURES_FILE)
    df = df.sort_values("as_of_date").drop_duplicates("crm_customer_id", keep="last")  # one row per customer
    df = df.sample(min(sample, len(df)), random_state=11).join(groups, on="crm_customer_id")
    X = model.frame(df)
    out = []
    for attr, (what, pos, neg) in PROXY_TARGETS.items():
        y = df[attr].map(lambda v: 1.0 if v in pos else (np.nan if v == NOT_RECORDED or (neg and v not in neg)
                                                          else 0.0))
        m = y.notna().to_numpy()
        yy = y[m].astype(int).to_numpy()
        if min(yy.sum(), len(yy) - yy.sum()) < 200:
            continue
        rng = np.random.default_rng(11)
        test = rng.random(len(yy)) < 0.3
        Xm = X[m]
        booster = lgb.train({"objective": "binary", "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 100,
                             "verbose": -1, "seed": 11}, lgb.Dataset(Xm[~test], yy[~test]), num_boost_round=200)
        auc = roc_auc_score(yy[test], booster.predict(Xm[test]))
        gain = sorted(zip(Xm.columns, booster.feature_importance("gain")), key=lambda t: -t[1])
        total = sum(g for _, g in gain) or 1.0
        out.append({"attr": attr, "what": what, "n": int(len(yy)), "positive": float(yy.mean()), "auc": float(auc),
                    "top": [(f, g / total) for f, g in gain[:3]]})
    return out


# ---------------------------------------------------------------- report

def _p(v, d=1):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{d}%}"


def _f(v, d=3, sign=False):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else (f"{v:+.{d}f}" if sign else f"{v:.{d}f}")


def build_report(con) -> str:
    groups = load_groups(con)
    models = model_rows(con)
    proxies = proxy_audit(groups)
    L: list[str] = []
    w = L.append
    findings: list[str] = []

    model_sections, crm_gap, tally = [], [], {}
    for title, df in models.items():
        summary, details = [], []
        for attr, label in ATTRIBUTES.items():
            t, overall = model_by_group(df, groups, attr)
            fo, fb = model_flags(t, overall, "ours"), model_flags(t, overall, "bank")
            tally[title] = [tally.get(title, [0, 0])[0] + len(fo), tally.get(title, [0, 0])[1] + len(fb)]
            for name, f in (("ours", fo), ("the bank's", fb)):
                if f:
                    findings.append(f"{title.split(' (')[0]}, {label.split(' (')[0].lower()}, {name}: {'; '.join(f)}")
            pt = t[~t["group"].isin((NOT_IN_CRM, NOT_RECORDED))]
            if attr == "gender_code":
                crm_gap.append((title, t[t["group"] == NOT_IN_CRM], overall))
            summary.append(
                f"| {label} | {len(pt)} | {_f(pt['auc_ours'].min())}–{_f(pt['auc_ours'].max())} | "
                f"{_f(pt['auc_bank'].min())}–{_f(pt['auc_bank'].max())} | "
                f"{_f(pt['cal_ours'].abs().max())} | {_f(pt['cal_bank'].abs().max())} | "
                f"{_f(pt['sel_ours'].min() / pt['sel_ours'].max(), 2)} | "
                f"{_f(pt['sel_bank'].min() / pt['sel_bank'].max(), 2)} | "
                f"{'; '.join(fo) or 'none'} | {'; '.join(fb) or 'none'} |")
            details.append(f"\n**{label}**\n\n| Group | Rows | Actual | Ours: mean (gap) | Bank: mean (gap) | AUC ours | "
                           f"AUC bank | Riskiest 20% ours | bank | Bad outcomes caught ours | bank |\n"
                           f"|---|---|---|---|---|---|---|---|---|---|---|")
            for r in t.itertuples():
                details.append(
                    f"| {r.group} | {r.n:,} | {_p(r.actual)} | {_f(r.mean_ours)} ({_f(r.cal_ours, sign=True)}) | "
                    f"{_f(r.mean_bank)} ({_f(r.cal_bank, sign=True)}) | {_f(r.auc_ours)} | {_f(r.auc_bank)} | "
                    f"{_p(r.sel_ours)} | {_p(r.sel_bank)} | {_p(r.tpr_ours)} | {_p(r.tpr_bank)} |")
        n_rows = len(df)
        overall_auc = (_auc(df["label"], df["ours"]), _auc(df["label"], df["bank"]))
        model_sections.append((title, n_rows, overall_auc, summary, details))

    dec_rows, dec_details, crm_queue = [], [], None
    for attr, label in ATTRIBUTES.items():
        t = decisions_by_group(groups, attr)
        if attr == "gender_code":
            crm_queue = t[t["group"] == NOT_IN_CRM]
        prot = t[~t["group"].isin((NOT_IN_CRM, NOT_RECORDED))]
        oe = prot["obs_exp"].dropna()
        bad = prot[(prot["obs_exp"] < FOUR_FIFTHS) | (prot["obs_exp"] > 1 / FOUR_FIFTHS)]
        flag = "; ".join(f"{r.group} {r.obs_exp:.2f} (n={r.cases:,})" for r in bad.itertuples()) or "none"
        if len(bad):
            findings.append(f"NBA queue, {label.split(' (')[0].lower()}: collections contact vs risk {flag}")
        dec_rows.append(f"| {label} | {len(prot)} | {_p(prot['collections'].min())}–{_p(prot['collections'].max())} | "
                        f"{_f(oe.min(), 2)}–{_f(oe.max(), 2)} | {_p(prot['support'].min())}–{_p(prot['support'].max())} | "
                        f"{flag} |")
        dec_details.append(f"\n**{label}**\n\n| Group | Cases | Mean risk | Collections contact | Contact vs risk "
                           f"(obs / exp) | Collections call | Support call | Wait | Hardship or vulnerable |\n"
                           f"|---|---|---|---|---|---|---|---|---|")
        for r in t.itertuples():
            dec_details.append(f"| {r.group} | {r.cases:,} | {_f(r.risk)} | {_p(r.collections)} | {_f(r.obs_exp, 2)} | "
                               f"{_p(r.calls)} | {_p(r.support)} | {_p(r.wait)} | {_p(r.flagged)} |")
    for p in proxies:
        if p["auc"] >= PROXY_AUC_FLAG:
            findings.append(f"Proxy audit: the case model's features predict {p['what']} with AUC {p['auc']:.3f} "
                            f"(mostly {', '.join(f for f, _ in p['top'])})")

    w("# Fairness report")
    w("")
    w(f"Generated by `python -m trustissues.nba.fairness` on the {config.AS_OF_DATE} data. Protected attributes come "
      "from the CRM and are joined back **only** for this report (`sources.protected_attributes`, readable only by "
      "`nba/fairness.py`); they are not model features and no rule or decision uses them. Only aggregates are "
      f"shown; groups under {MIN_GROUP} people are left out. Vulnerability is not compared: it is a safeguard that "
      "deliberately changes treatment (POL-COLL-004 §4.5).")
    w("")
    w("This is a screen for review, not a legal test. A flag means a difference large enough to look into: "
      f"calibration more than {CALIBRATION_TOLERANCE} away from overall, AUC more than {AUC_TOLERANCE} below "
      f"overall, a lowest / highest group ratio below {FOUR_FIFTHS} (the four-fifths rule of thumb), contact more "
      f"than {1 / FOUR_FIFTHS - 1:.0%} away from what the group's risk predicts, or features that predict an "
      f"attribute with AUC {PROXY_AUC_FLAG} or more. A model flag also needs the difference to be at least {Z} "
      f"standard errors, and an AUC flag {MIN_AUC_CLASS} rows of each outcome, so small groups are shown but not "
      "flagged on noise. Groups differ in how often they break promises or fail to cure, "
      "so different selection rates are expected (each flag shows the groups' actual rates next to them); calibration "
      "within each group, and accuracy, are what say a score treats a group fairly.")
    w("")
    w("## 1. Findings")
    w("")
    for title, (n_ours, n_bank) in tally.items():
        w(f"- **{title.split(' (')[0]}**: {n_ours} screening flags for our model, {n_bank} for the bank's, across "
          f"{len(ATTRIBUTES)} attributes.")
    w("")
    w("\n".join(f"- {f}" for f in findings) if findings else "- No group crossed a screening threshold.")
    w("")
    w("## 2. The models, by group")
    w("")
    w("Holdout rows scored by both our model and the bank's. *Gap* = mean score minus actual rate (positive: the "
      "score overstates risk for that group). *Riskiest 20%* = share of the group in the overall riskiest 20%. "
      "*Bad outcomes caught* = share of the group's broken promises / uncured cases that are in the riskiest 20%.")
    for title, n_rows, (auc_o, auc_b), summary, details in model_sections:
        w("")
        w(f"### {title}")
        w("")
        w(f"{n_rows:,} holdout rows; overall AUC {auc_o:.3f} ours, {auc_b:.3f} the bank's.")
        w("")
        w("| Attribute | Groups | AUC range ours | AUC range bank | Largest calibration gap ours | bank | "
          "Selection ratio ours | bank | Flags ours | Flags bank |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        L.extend(summary)
        w("")
        w("<details><summary>Every group</summary>")
        L.extend(details)
        w("")
        w("</details>")
    w("")
    w("## 3. The morning NBA queue, by group")
    w("")
    w(f"The {config.AS_OF_DATE} queue (`warehouse/p3/nba_decisions.parquet`). *Collections contact* = a call, SMS, "
      "email, push or letter asking for payment. *Contact vs risk* compares that rate, for cases without a hardship or "
      "vulnerability flag, with the rate their risk deciles get overall (1.00 = exactly what risk predicts). Support "
      "calls go only to hardship and vulnerable cases, so they follow those flags.")
    w("")
    w("| Attribute | Groups | Collections contact range | Contact vs risk range | Support call range | Flags |")
    w("|---|---|---|---|---|---|")
    L.extend(dec_rows)
    w("")
    w("<details><summary>Every group</summary>")
    L.extend(dec_details)
    w("")
    w("</details>")
    w("")
    w("## 4. Cases whose customer is not in the CRM")
    w("")
    w("Not a protected group, so left out of the screens above, but worth knowing: these cases point at a CRM customer "
      "that does not exist (data quality check R1). They are rows of every table above, labelled *not in CRM*.")
    w("")
    for title, g, overall in crm_gap:
        if len(g):
            r = g.iloc[0]
            w(f"- {title.split(' (')[0]}: {r['n']:,} holdout rows; actual {r['actual']:.1%}; our score's gap "
              f"{r['cal_ours']:+.3f} (overall {overall['ours']['cal']:+.3f}), the bank's {r['cal_bank']:+.3f}; "
              f"{r['sel_ours']:.1%} in our riskiest 20%.")
    if crm_queue is not None and len(crm_queue):
        r = crm_queue.iloc[0]
        w(f"- Morning queue: {r['cases']:,} cases, {r['collections']:.1%} get any contact and {r['wait']:.1%} wait. With "
          "no CRM record there is no consent on any channel, so the rules allow nothing but waiting (default deny).")
    w("")
    w("These close once identity resolution links these cases to a golden customer record.")
    w("")
    w("## 5. Proxy audit: can the features stand in for a protected attribute?")
    w("")
    w("A small model is trained to predict each attribute from the case model's own features (one row per customer, "
      "a sample of customers with open or past cases, 30% held out). AUC 0.5 means the features carry no trace of the "
      "attribute; the closer to 1, the more a feature could stand in for it.")
    w("")
    w("| Attribute | Predicting | Customers | Share positive | Held-out AUC | Features that carry it most (share of gain) |")
    w("|---|---|---|---|---|---|")
    for p in proxies:
        w(f"| {ATTRIBUTES[p['attr']].split(' (')[0]} | {p['what']} | {p['n']:,} | {_p(p['positive'])} | "
          f"{p['auc']:.3f} | {', '.join(f'`{f}` {g:.0%}' for f, g in p['top'])} |")
    w("")
    return "\n".join(L)


def main() -> int:
    con = connect(read_only=True)
    sources.register(con)
    text = build_report(con)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    print(text[text.index("## 1. Findings"):text.index("## 2.")])
    print(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
