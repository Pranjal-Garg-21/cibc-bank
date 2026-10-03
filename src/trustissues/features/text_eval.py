"""Score the LLM text features against the public labels and write reports/text_feature_eval.md. Owner: P4.

Text features are used by the model (P3) only if they pass here (implementation plan §5). The labels are the
release's `labels/agent_notes_labels_public_500.csv` and `labels/call_transcripts_labels_public_500.csv`; texts that
were not eligible for an LLM (redaction, retention) have no features and are reported as not covered.

    python -m trustissues.features.text_extract --scope labelled
    python -m trustissues.features.text_eval
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd

from trustissues.common import config

from .text_extract import PROMPT_VERSION, intent_band

# minimum scores for the features to be used by the model (the gate)
GATE = {"hardship_signal F1": 0.80, "stated_delay_reason accuracy": 0.70, "dispute_mentioned F1": 0.80,
        "ptp_mentioned F1": 0.85}


def load_labels() -> pd.DataFrame:
    base = config.maple_data_dir() / "labels"
    n = pd.read_csv(base / "agent_notes_labels_public_500.csv", dtype={"note_id": str})
    t = pd.read_csv(base / "call_transcripts_labels_public_500.csv", dtype={"transcript_id": str})
    n = n.rename(columns={"note_id": "source_id"}).assign(source_type="note")
    t = t.rename(columns={"transcript_id": "source_id"}).assign(source_type="transcript")
    cols = ["source_id", "source_type", "label_hardship", "label_hardship_reason", "label_stated_delay_reason",
            "label_ptp_mentioned", "label_ptp_intent_strength", "label_dispute", "label_cease_request",
            "label_vulnerability", "label_next_step", "label_sentiment"]
    return pd.concat([n[cols], t[cols]], ignore_index=True)


def load_features(con, prompt_version: str = PROMPT_VERSION) -> pd.DataFrame:
    return con.execute("SELECT * FROM gold.text_features WHERE prompt_version = ?", [prompt_version]).df()


@dataclass
class Score:
    name: str
    value: float | None
    n: int
    detail: str = ""


def _b(s: pd.Series) -> pd.Series:
    return s.map(lambda v: v if isinstance(v, bool) else str(v).strip().lower() == "true" if pd.notna(v) else False)


def binary(name: str, y: pd.Series, p: pd.Series) -> Score:
    y, p = _b(y), _b(p)
    tp, fp, fn = int((y & p).sum()), int((~y & p).sum()), int((y & ~p).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (None if tp + fp + fn == 0 else 0.0)
    return Score(f"{name} F1", f1, len(y), f"precision {_f(prec)}, recall {_f(rec)}, {int(y.sum())} positive")


def accuracy(name: str, y: pd.Series, p: pd.Series) -> Score:
    y, p = y.fillna("(none)").astype(str), p.fillna("(none)").astype(str)
    return Score(f"{name} accuracy", float((y == p).mean()) if len(y) else None, len(y))


def _f(x) -> str:
    return "-" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x:.2f}"


def evaluate(labels: pd.DataFrame, feats: pd.DataFrame) -> tuple[dict[str, list[Score]], dict, pd.DataFrame]:
    m = labels.merge(feats, on=["source_id", "source_type"], how="left", indicator=True)
    covered = m[m["_merge"] == "both"].copy()
    coverage = {k: (int((covered.source_type == k).sum()), int((labels.source_type == k).sum()))
                for k in ("note", "transcript")}
    out = {}
    for kind, g in [("notes", covered[covered.source_type == "note"]),
                    ("transcripts", covered[covered.source_type == "transcript"]), ("all", covered)]:
        hs_label = g.label_hardship.isin(["clear", "possible"])
        scores = [
            binary("hardship_signal", hs_label, g.hardship_signal),
            accuracy("hardship level (none/possible/clear)", g.label_hardship, g.hardship_level),
            accuracy("hardship_reason (where labelled)", g.label_hardship_reason[g.label_hardship_reason.notna()],
                     g.hardship_reason[g.label_hardship_reason.notna()]),
            accuracy("stated_delay_reason", g.label_stated_delay_reason, g.stated_delay_reason),
            binary("ptp_mentioned", g.label_ptp_mentioned, g.ptp_mentioned),
            binary("dispute_mentioned", g.label_dispute, g.dispute_mentioned),
            binary("cease_request", g.label_cease_request, g.cease_request),
            binary("vulnerability", g.label_vulnerability, g.vulnerability),
            accuracy("next_step", g.label_next_step, g.next_step),
            accuracy("sentiment", g.label_sentiment, g.sentiment),
        ]
        both = g[g.label_ptp_intent_strength.notna() & g.ptp_intent_score.notna()]
        if len(both):
            mae = float((both.label_ptp_intent_strength - both.ptp_intent_score).abs().mean())
            bands = both.label_ptp_intent_strength.map(lambda v: intent_band(v, True))
            scores.append(Score("ptp intent mean abs error (0-1, lower is better)", mae, len(both)))
            scores.append(accuracy("ptp_intent_strength band", bands, both.ptp_intent_strength))
        scores.append(Score("evidence sentence kept (verbatim)", float(g.evidence_sentence.notna().mean()) if len(g) else None,
                            len(g)))
        out[kind] = scores
    gate = {k: next((s.value for s in out["all"] if s.name == k), None) for k in GATE}
    return out, {"coverage": coverage, "gate": gate}, covered


def confusion(covered: pd.DataFrame, label: str, pred: str) -> str:
    t = pd.crosstab(covered[label].fillna("(none)"), covered[pred].fillna("(none)"))
    head = "| label \\ predicted | " + " | ".join(map(str, t.columns)) + " |"
    sep = "|---|" + "---:|" * len(t.columns)
    rows = [f"| {i} | " + " | ".join(str(v) for v in r) + " |" for i, r in zip(t.index, t.values)]
    return "\n".join([head, sep, *rows])


def write_report(scores, meta, covered, path, prompt_version: str = PROMPT_VERSION) -> None:
    passed = all(v is not None and v >= GATE[k] for k, v in meta["gate"].items())
    cov = meta["coverage"]
    lines = [
        "# Text feature evaluation", "",
        f"LLM text features (`src/trustissues/features/text_extract.py`, prompt `{prompt_version}`) scored against the "
        "release's public labels (500 notes, 500 transcripts). Built by `python -m trustissues.features.text_eval`.", "",
        f"**Coverage:** {cov['note'][0]} of {cov['note'][1]} labelled notes and {cov['transcript'][0]} of "
        f"{cov['transcript'][1]} labelled transcripts have features. The rest were not eligible to reach an LLM "
        "(empty text, `needs_review`, past retention) and get no text features.", "",
        f"## Gate: **{'PASS' if passed else 'FAIL'}**", "",
        "| check | needed | result |", "|---|---:|---:|",
        *[f"| {k} | {GATE[k]:.2f} | {_f(v)} |" for k, v in meta["gate"].items()], "",
        "## Scores", "", "| field | notes | transcripts | all | detail (all) |", "|---|---:|---:|---:|---|",
    ]
    names = [s.name for s in scores["all"]]
    for nm in names:
        get = {k: next((s for s in v if s.name == nm), None) for k, v in scores.items()}
        a = get["all"]
        lines.append(f"| {nm} | {_f(get['notes'].value if get['notes'] else None)} | "
                     f"{_f(get['transcripts'].value if get['transcripts'] else None)} | {_f(a.value)} | "
                     f"n={a.n}{'; ' + a.detail if a.detail else ''} |")
    lines += ["", "## Stated reason for paying late: confusion", "",
              confusion(covered, "label_stated_delay_reason", "stated_delay_reason"), "",
              "## Hardship level: confusion", "", confusion(covered, "label_hardship", "hardship_level"), "",
              "## How to read this", "",
              "- `hardship_signal` is true for `clear` or `possible` hardship, as in the labels.",
              "- `stated_delay_reason` follows fixed rules after the model answers: a dispute is the reason when "
              "one is raised; job loss, reduced hours and EI waiting are `reduced_income`; illness, disability, "
              "separation, rate reset and disaster are `other`. The public labels follow the same rules.",
              "- `ptp_intent_strength` bands: strong >= 0.70, moderate 0.55-0.70, weak below (labels: 0.85 firm, "
              "0.45 hedged).",
              "- The labels are not perfect (e.g. \"serious illness in family\" labelled no hardship), so a few "
              "disagreements are the label's, not the model's.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    from trustissues.common.db import connect

    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-version", default=PROMPT_VERSION)
    a = ap.parse_args()
    con = connect(read_only=True)
    scores, meta, covered = evaluate(load_labels(), load_features(con, a.prompt_version))
    path = config.REPO_ROOT / "reports" / "text_feature_eval.md"
    write_report(scores, meta, covered, path, a.prompt_version)
    for s in scores["all"]:
        print(f"{s.name:<55} {_f(s.value):>6}   n={s.n}  {s.detail}")
    print(f"\ngate: {meta['gate']}\nreport: {path}")


if __name__ == "__main__":
    main()
