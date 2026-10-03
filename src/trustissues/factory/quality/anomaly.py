"""Anomaly detection: days whose row counts or null rates are far from normal, explained by the operations log.

    python -m trustissues.factory.quality.anomaly          # writes curated._anomaly_days, curated._anomaly_episodes,
                                                          # reports/anomaly_report.md

Plan §3.5: "Flags row counts and null rates more than 3 sigma off recent loads and matches them to ops_events.
Explained anomalies are labelled; unexplained ones alert." The data was loaded once, so "recent loads" means the
days (or, for monthly files, the loads) before each point inside the data itself.

For every series in config/anomaly.yaml:
  row counts  per day (per slice, e.g. outbound calls). Baseline: the same weekday over the previous 8 weeks, so a busy
              Friday is compared with Fridays. Monthly / quarterly files: the previous 6 loads.
  null rates  per day, for every column of the table. Baseline: the previous 28 days (or 6 loads).
A point is flagged when its robust z-score (median and MAD) is beyond the threshold AND the change is material
(30% for counts, 20 points for null rates), so small-volume noise does not flood the list.

Consecutive flagged days of one metric form an episode. Each episode is labelled:
  explained    it overlaps an ops_events window (plus a day of lag) and the event lists the table as affected
  calendar     every day of it is a statutory holiday (from contact_history.stat_holiday_flag)
  unexplained  neither: an alert, and a lead for the issues the operations log does not list
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta

import pandas as pd
import yaml

from trustissues.common import config
from trustissues.common.db import connect

CONFIG = config.CONFIG_DIR / "anomaly.yaml"
REPORT = config.REPO_ROOT / "reports" / "anomaly_report.md"


def load_config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _q(c: str) -> str:
    return '"' + c.replace('"', '""') + '"'


# ------------------------------------------------------------------------------------------------ series extraction

def _bounds(cfg: dict, s: dict) -> tuple[date, date, date | None]:
    end = config.AS_OF_DATE - timedelta(days=cfg["end_offset_days"])
    start = end - timedelta(days=cfg["window_days"])
    if s["mode"] == "loads":
        return start, end, None  # load series read all history: monthly files need older loads as a baseline
    look = max(cfg["counts"]["baseline_weeks"], cfg["nulls"]["baseline_weeks"]) * 7 + 1
    return start, end, start - timedelta(days=look)


def _where(s: dict, lo: date | None, end: date) -> str:
    parts = [f"{_q(s['ts'])} IS NOT NULL", f"CAST({_q(s['ts'])} AS DATE) <= DATE '{end}'"]
    if lo:
        parts.append(f"CAST({_q(s['ts'])} AS DATE) >= DATE '{lo}'")
    if s.get("where"):
        parts.append(f"({s['where']})")
    return " AND ".join(parts)


def counts_frame(con, cfg: dict, s: dict) -> pd.DataFrame:
    """day, slice, value. Daily mode fills missing days with 0 (a day with no rows is the strongest anomaly)."""
    start, end, lo = _bounds(cfg, s)
    sl = s.get("slice") or "'all'"
    df = con.execute(f"""SELECT CAST({_q(s['ts'])} AS DATE) AS day, coalesce(CAST({sl} AS VARCHAR), '(none)') AS slice,
                                count(*) AS value
                         FROM {s['table']} WHERE {_where(s, lo, end)} GROUP BY ALL""").df()
    if not df.empty:
        df["day"] = pd.to_datetime(df["day"]).dt.date
    if df.empty or s["mode"] != "daily":
        return df
    days = pd.date_range(lo, end, freq="D").date
    full = pd.MultiIndex.from_product([days, df["slice"].unique()], names=["day", "slice"])
    return df.set_index(["day", "slice"]).reindex(full, fill_value=0).reset_index()


def null_frame(con, cfg: dict, s: dict) -> pd.DataFrame:
    """day, slice, column, rows, nulls: per day, per slice, per column (metadata and ignored columns left out)."""
    start, end, lo = _bounds(cfg, s)
    schema, table = s["table"].split(".")
    ignore = re.compile(s["ignore_columns"]) if s.get("ignore_columns") else None
    cols = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = ? "
                                      "AND table_name = ? ORDER BY ordinal_position", [schema, table]).fetchall()
            if not r[0].startswith("_") and r[0] != s["ts"] and not (ignore and ignore.match(r[0]))]
    sl = s.get("slice") or "'all'"
    aggs = ", ".join(f"count(*) - count({_q(c)}) AS {_q('n_' + c)}" for c in cols)
    wide = con.execute(f"""SELECT CAST({_q(s['ts'])} AS DATE) AS day, coalesce(CAST({sl} AS VARCHAR), '(none)') AS slice,
                                  count(*) AS rows, {aggs}
                           FROM {s['table']} WHERE {_where(s, lo, end)} GROUP BY ALL""").df()
    if wide.empty:
        return pd.DataFrame(columns=["day", "slice", "column", "rows", "nulls"])
    wide["day"] = pd.to_datetime(wide["day"]).dt.date
    long = wide.melt(id_vars=["day", "slice", "rows"], var_name="column", value_name="nulls")
    long["column"] = long["column"].str[2:]
    return long


# ------------------------------------------------------------------------------------------------ scoring

def robust_baseline(history: pd.Series) -> tuple[float, float]:
    med = float(history.median())
    mad = float((history - med).abs().median()) * 1.4826
    return med, mad


def score_counts(df: pd.DataFrame, cfg: dict, mode: str, start: date) -> pd.DataFrame:
    """Flag count points. Daily: baseline = same weekday, previous N weeks. Loads: previous N loads."""
    c = cfg["counts"]
    out = []
    for sl, g in df.sort_values("day").groupby("slice"):
        g = g.reset_index(drop=True)
        if mode == "daily":
            g["wd"] = pd.to_datetime(g["day"]).dt.weekday
            groups = [x for _, x in g.groupby("wd")]
            k = c["baseline_weeks"]
        else:
            groups, k = [g], c["baseline_loads"]
        for x in groups:
            vals = x["value"].astype(float).tolist()
            for i in range(len(x)):
                day = x["day"].iloc[i]
                if day < start or i < max(3, k // 2):
                    continue
                med, mad = robust_baseline(pd.Series(vals[max(0, i - k):i]))
                if med < c["min_baseline"]:
                    continue
                v = vals[i]
                spread = max(mad, med ** 0.5, 0.05 * med)  # Poisson noise floor
                z = (v - med) / spread
                rel = (v - med) / med
                if abs(z) >= cfg["z_threshold"] and abs(rel) >= c["min_relative_change"]:
                    out.append({"day": day, "slice": sl, "metric": "row_count", "value": v, "baseline": med,
                                "z": round(z, 2), "change": round(rel, 3)})
    return pd.DataFrame(out)


def score_nulls(df: pd.DataFrame, cfg: dict, mode: str, start: date) -> pd.DataFrame:
    """Null rate per slice and column. Daily: baseline = the same weekday over the previous weeks (the contact mix
    changes by weekday). Loads: the previous loads."""
    n = cfg["nulls"]
    out = []
    df = df[df["rows"] >= n["min_rows"]].copy()
    df["rate"] = df["nulls"] / df["rows"]
    if mode == "daily":
        df["wd"] = pd.to_datetime(df["day"]).dt.weekday
        keys, k = ["slice", "column", "wd"], n["baseline_weeks"]
    else:
        keys, k = ["slice", "column"], n["baseline_loads"]
    for key, g in df.sort_values("day").groupby(keys):
        sl, col = key[0], key[1]
        rates, days = g["rate"].tolist(), g["day"].tolist()
        for i in range(len(g)):
            if days[i] < start or i < max(3, k // 2):
                continue
            med, mad = robust_baseline(pd.Series(rates[max(0, i - k):i]))
            diff = rates[i] - med
            spread = max(mad, 0.02)
            if abs(diff / spread) >= cfg["z_threshold"] and abs(diff) >= n["min_change"]:
                out.append({"day": days[i], "slice": sl, "metric": f"null_rate:{col}", "value": round(rates[i], 4),
                            "baseline": round(med, 4), "z": round(diff / spread, 2), "change": round(diff, 3)})
    return pd.DataFrame(out)


# ------------------------------------------------------------------------------------------------ explanation

def episodes(days: pd.DataFrame, gap_days: int = 3) -> pd.DataFrame:
    """Merge flagged days of one metric moving the SAME way into episodes. Gaps of up to `gap_days` are bridged, so a
    sustained level shift is one episode; a dip and a rise next to each other are never merged."""
    rows = []
    days = days.assign(sign=days["z"].map(lambda z: "up" if z > 0 else "down"))
    for (series, sl, metric, _), g in days.sort_values("day").groupby(["series", "slice", "metric", "sign"]):
        cur = None
        for _, r in g.iterrows():
            gap = gap_days if g["mode"].iloc[0] == "daily" else 45
            if cur and (r["day"] - cur["end"]).days <= gap:
                cur["end"] = r["day"]
                cur["days"] += 1
                cur["worst_z"] = max(cur["worst_z"], r["z"], key=abs)
                cur["changes"].append(r["change"])
            else:
                if cur:
                    rows.append(cur)
                cur = {"series": series, "table": r["table"], "slice": sl, "metric": metric, "start": r["day"],
                       "end": r["day"], "days": 1, "worst_z": r["z"], "changes": [r["change"]],
                       "direction": "up" if r["z"] > 0 else "down"}
        if cur:
            rows.append(cur)
    out = pd.DataFrame(rows)
    if not out.empty:
        out["change"] = out["changes"].map(lambda c: max(c, key=abs))
        out = out.drop(columns=["changes"])
    return out


def explain(ep: pd.DataFrame, events: list[dict], holidays: set, lag: int, long_days: int = 14) -> pd.DataFrame:
    """An event explains an episode when it lists the table and the dates overlap (plus `lag` days). A long event
    (layoffs, a policy change) can shift volumes but cannot empty a column, so it explains row counts only."""
    labels, reasons = [], []
    for _, e in ep.iterrows():
        tname = e["table"].split(".")[-1]
        hit = [ev for ev in events
               if tname in ev["tables"] and e["start"] <= ev["end"] + timedelta(days=lag) and e["end"] >= ev["start"]
               and (e["metric"] == "row_count" or (ev["end"] - ev["start"]).days <= long_days)]
        span = [e["start"] + timedelta(days=i) for i in range((e["end"] - e["start"]).days + 1)]
        if hit:
            labels.append("explained")
            reasons.append(", ".join(f"{ev['id']} ({ev['description'][:70]})" for ev in hit))
        elif span and all(d in holidays for d in span):
            labels.append("calendar")
            reasons.append("statutory holiday")
        else:
            labels.append("unexplained")
            reasons.append("")
    ep = ep.copy()
    ep["status"], ep["explained_by"] = labels, reasons
    return mark_recurring(ep)


def mark_recurring(ep: pd.DataFrame, tolerance: int = 2, min_months: int = 3) -> pd.DataFrame:
    """A monthly business cycle is not an anomaly: statement and due dates make card cases jump on the same days every
    month. An unexplained episode whose metric moves the same way at the same point of the month (+/- `tolerance`
    days) in at least `min_months` other months is labelled `recurring`."""
    ep = ep.copy()

    def dom_close(a: int, b: int) -> bool:
        d = abs(a - b)
        return min(d, 31 - d) <= tolerance

    for key, g in ep.groupby(["series", "slice", "metric", "direction"]):
        for idx, r in g.iterrows():
            if r["status"] != "unexplained":
                continue
            months = {(o["start"].year, o["start"].month) for _, o in g.iterrows()
                      if o.name != idx and dom_close(o["start"].day, r["start"].day)
                      and (o["start"].year, o["start"].month) != (r["start"].year, r["start"].month)}
            if len(months) >= min_months:
                ep.at[idx, "status"] = "recurring"
                ep.at[idx, "explained_by"] = f"same change around day {r['start'].day} in {len(months)} other months"
    return ep


def load_events(con) -> list[dict]:
    out = []
    for eid, start, end, tables, desc in con.execute(
            "SELECT event_id, start_ts, end_ts, affected_tables, description FROM curated.ops_events").fetchall():
        out.append({"id": eid, "start": pd.Timestamp(start).date(), "end": pd.Timestamp(end or start).date(),
                    "tables": set(json.loads(tables) if tables else []), "description": desc or ""})
    return out


def load_holidays(con) -> set:
    return {r[0] for r in con.execute("SELECT DISTINCT CAST(contact_ts_utc AS DATE) FROM curated.contact_history "
                                      "WHERE stat_holiday_flag").fetchall()}


# ------------------------------------------------------------------------------------------------ run

def detect(con, cfg: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = cfg or load_config()
    flagged, checked = [], 0
    for s in cfg["series"]:
        t0 = time.time()
        start, _, _ = _bounds(cfg, s)
        c = counts_frame(con, cfg, s)
        if not c.empty and s.get("count_exclude_slices"):
            c = c[~c["slice"].isin(s["count_exclude_slices"])]
        parts = [score_counts(c, cfg, s["mode"], start)] if not c.empty else []
        checked += int((c["day"] >= start).sum()) if not c.empty else 0
        if s.get("null_rates"):
            nf = null_frame(con, cfg, s)
            if s.get("nulls_min_age_days") and not nf.empty:  # records too young to have every field filled in yet
                nf = nf[nf["day"] <= config.AS_OF_DATE - timedelta(days=s["nulls_min_age_days"])]
            parts.append(score_nulls(nf, cfg, s["mode"], start))
            checked += int((nf["day"] >= start).sum()) if not nf.empty else 0
        for p in parts:
            if not p.empty:
                p["series"], p["table"], p["mode"] = s["name"], s["table"], s["mode"]
                flagged.append(p)
        print(f"  {s['name']:18s} {sum(len(p) for p in parts):5d} flagged points  {time.time() - t0:5.1f}s", flush=True)
    days = pd.concat(flagged, ignore_index=True) if flagged else pd.DataFrame(
        columns=["day", "slice", "metric", "value", "baseline", "z", "change", "series", "table", "mode"])
    ep = episodes(days, cfg.get("episode_gap_days", 3)) if not days.empty else pd.DataFrame()
    if not ep.empty:
        ep = explain(ep, load_events(con), load_holidays(con), cfg["event_lag_days"], cfg.get("long_event_days", 14))
    days.attrs["checked"] = checked
    return days, ep


def save(con, days: pd.DataFrame, ep: pd.DataFrame) -> None:
    con.register("_d", days)
    con.execute("CREATE OR REPLACE TABLE curated._anomaly_days AS SELECT * FROM _d")
    con.unregister("_d")
    if ep.empty:
        ep = pd.DataFrame(columns=["series", "table", "slice", "metric", "start", "end", "days", "worst_z",
                                   "direction", "change", "status", "explained_by"])
    con.register("_e", ep)
    con.execute("CREATE OR REPLACE TABLE curated._anomaly_episodes AS SELECT * FROM _e")
    con.unregister("_e")
    con.execute("CREATE OR REPLACE TABLE curated._anomaly_run AS SELECT ? AS points_checked, now() AS run_ts",
                [int(days.attrs.get("checked", 0))])


def render(ep: pd.DataFrame, checked: int, cfg: dict) -> str:
    end = config.AS_OF_DATE - timedelta(days=cfg["end_offset_days"])
    start = end - timedelta(days=cfg["window_days"])
    L = ["# Anomaly report", "",
         f"Generated by `python -m trustissues.factory.quality.anomaly` from the warehouse, for {start} to {end}. "
         "Settings: `config/anomaly.yaml`. Do not edit by hand.", "",
         "A point is a day (or, for monthly files, a load) of one series: a row count per slice, or one column's "
         f"null rate. {checked:,} points were checked. A point is flagged when it is more than "
         f"{cfg['z_threshold']} robust standard deviations from its baseline and the change is material; consecutive "
         "flagged points form an episode.", ""]
    if ep.empty:
        return "\n".join(L + ["No anomalies found."]) + "\n"
    by = ep["status"].value_counts().to_dict()
    L += ["## Summary", "", "| Status | Episodes | Meaning |", "|---|---:|---|",
          f"| unexplained | {by.get('unexplained', 0)} | nothing in the operations log or the calendar covers it: investigate |",
          f"| explained | {by.get('explained', 0)} | overlaps a logged event that lists the table |",
          f"| recurring | {by.get('recurring', 0)} | the same change at the same point of the month in 3+ other months (billing and due-date cycles) |",
          f"| calendar | {by.get('calendar', 0)} | statutory holiday |", ""]

    def table(rows):
        out = ["| Series | Slice | Metric | From | To | Days | Change | Worst z | Explained by |",
               "|---|---|---|---|---|---:|---:|---:|---|"]
        for _, r in rows.iterrows():
            ch = f"{r['change']:+.0%}" if r["metric"] == "row_count" else f"{r['change']:+.0%} pts"
            out.append(f"| {r['series']} | {r['slice']} | {r['metric']} | {r['start']} | {r['end']} | {r['days']} | "
                       f"{ch} | {r['worst_z']:+.1f} | {r['explained_by'] or ''} |")
        return out

    order = lambda d: d.assign(a=d["worst_z"].abs()).sort_values(["a"], ascending=False).drop(columns="a")
    for status, title in (("unexplained", "Unexplained (alerts)"), ("explained", "Explained by the operations log"),
                          ("recurring", "Recurring monthly cycles"), ("calendar", "Statutory holidays")):
        rows = ep[ep["status"] == status]
        L += [f"## {title}", ""]
        L += (table(order(rows)) if not rows.empty else ["None."]) + [""]
    events = sorted({e.split(" (")[0] for x in ep["explained_by"] for e in x.split(", ") if x})
    L += ["## Operations-log events seen in the data", "",
          "Events with at least one explained episode: " + (", ".join(events) if events else "none") + ". "
          "An event with no episode may still be in the data at a size below the thresholds; the data quality report "
          "checks each logged event directly (section A).", ""]
    return "\n".join(L) + "\n"


def run() -> dict:
    cfg = load_config()
    con = connect()
    t0 = time.time()
    days, ep = detect(con, cfg)
    save(con, days, ep)
    checked = int(days.attrs.get("checked", 0))
    con.close()
    REPORT.write_text(render(ep, checked, cfg), encoding="utf-8")
    out = {"points_checked": checked, "flagged_points": len(days), "episodes": len(ep),
           **(ep["status"].value_counts().to_dict() if not ep.empty else {}), "secs": round(time.time() - t0)}
    print(out)
    return out


def main() -> None:
    run()


if __name__ == "__main__":
    main()
