"""Identity review: the open pairs from gold.identity_review_queue, and decisions appended to the CSV that the
identity build reads (factory/identity/review_queue.py). The UI never writes to the warehouse here; a decision
takes effect on the next identity run.
"""
from __future__ import annotations

import csv
import re
import threading
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..deps import current_user, require_reauthentication, require_role, utc_now, warehouse
from ..errors import ApiError, not_connected
from ..security import clean_text, csv_safe, mask_phone

router = APIRouter(prefix="/api/identity", tags=["identity"])

_CSV_LOCK = threading.Lock()
PAIR_ID = r"^[0-9a-f]{32}$"
_TAG = re.compile(r"\[([A-Z_]+)\]\s*([^\[]*)")
LABELS = {"SRC": "Source", "NAME": "Name", "DOB": "Date of birth", "PHONE": "Phone", "POSTAL": "Postal code",
          "EMPLOYER": "Employer"}


def _fields() -> list[str]:
    """The CSV header the identity build reads. Imported lazily, like every backend module."""
    try:
        from trustissues.factory.identity.review_queue import FIELDS
    except ImportError as e:
        raise not_connected("Identity review") from e
    return FIELDS


def _reviewer(request: Request, con):
    user = current_user(request, con)
    require_role(user, request.app.state.settings.identity_reviewer_roles)
    return user


def parse_record(serial: str | None) -> list[dict]:
    """'[SRC] crm [NAME] A B [PHONE] +1...' -> [{field, label, value}], phones masked to the last four digits."""
    out = []
    for tag, value in _TAG.findall(serial or ""):
        value = value.strip()
        if tag == "PHONE":
            value = ", ".join(mask_phone(p) for p in value.split(",") if p.strip())
        out.append({"field": tag.lower(), "label": LABELS.get(tag, tag.title()), "value": value})
    return out


def decided_pairs(path) -> dict[tuple[str, str], str]:
    """Pairs already decided in the CSV (latest decision wins), so the queue can show them as done."""
    if not path.exists():
        return {}
    with _CSV_LOCK, open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {(min(r["node_a"], r["node_b"]), max(r["node_a"], r["node_b"])): r["decision"]
            for r in rows if r.get("node_a") and r.get("node_b")}


@router.get("/queue")
def queue(request: Request, reason: str | None = Query(None, max_length=40), limit: int = Query(50, ge=1, le=100),
          offset: int = Query(0, ge=0, le=1_000_000), include_decided: bool = False):
    s = request.app.state.settings
    with warehouse(s) as con:
        _reviewer(request, con)
        counts = dict(con.execute("SELECT reason, count(*) FROM gold.identity_review_queue GROUP BY 1 ORDER BY 2 DESC")
                      .fetchall())
        where, params = ("WHERE reason = ?", [reason]) if reason else ("", [])
        rows = con.execute(f"""
            SELECT pair_id, node_a, node_b, reason, source_system_a, source_system_b, splink_probability, llm_match,
                   llm_status
            FROM gold.identity_review_queue {where}
            ORDER BY splink_probability DESC NULLS LAST, pair_id""", params).fetchall()
    done = decided_pairs(s.decisions_csv)
    items = []
    for r in rows:
        decision = done.get((min(r[1], r[2]), max(r[1], r[2])))
        if decision and not include_decided:
            continue
        llm_ok = r[8] != "unavailable" and r[7] is not None  # no verdict is not the same as "different"
        items.append({"pair_id": r[0], "reason": r[3], "source_a": r[4], "source_b": r[5],
                      "splink_probability": r[6], "llm_match": r[7] if llm_ok else None,
                      "llm_available": llm_ok, "decision": decision})
    return {"total": len(items), "counts_by_reason": counts, "items": items[offset:offset + limit]}


@router.get("/pairs/{pair_id}")
def pair(pair_id: str, request: Request):
    s = request.app.state.settings
    if not re.match(PAIR_ID, pair_id):
        raise ApiError(404, "not_found", "No such pair.")
    with warehouse(s) as con:
        user = _reviewer(request, con)
        row = con.execute("""
            SELECT pair_id, node_a, node_b, reason, record_a, record_b, splink_probability, splink_explanation,
                   llm_match, llm_probability, llm_reason, llm_status, detail
            FROM gold.identity_review_queue WHERE pair_id = ?""", [pair_id]).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "No such pair.")
    request.app.state.access_log.record(action="open_identity_pair", user_id=user.agent_id, role=user.role,
                                        object_id=pair_id, request_id=request.state.request_id)
    llm_ok = row[11] != "unavailable" and row[8] is not None
    decision = decided_pairs(s.decisions_csv).get((min(row[1], row[2]), max(row[1], row[2])))
    return {
        "pair_id": row[0], "reason": row[3], "decision": decision,
        "record_a": parse_record(row[4]), "record_b": parse_record(row[5]),
        "splink_probability": row[6],
        "splink_explanation": [p.strip() for p in (row[7] or "").split("|") if p.strip()],
        # A failed LLM call stores its error text in llm_reason: never shown, only "unavailable".
        "llm": {"available": llm_ok, "match": row[8] if llm_ok else None,
                "probability": row[9] if llm_ok else None, "reason": row[10] if llm_ok else None},
        "detail": row[12],
    }


class Decision(BaseModel):
    pair_id: str = Field(pattern=PAIR_ID)
    decision: Literal["match", "no_match", "unresolved"]
    note: str = Field("", max_length=500)


@router.post("/decisions")
def decide(body: Decision, request: Request):
    app = request.app.state
    s = app.settings
    fields = _fields()
    with warehouse(s) as con:
        user = _reviewer(request, con)
        row = con.execute("SELECT node_a, node_b, evidence_key FROM gold.identity_review_queue WHERE pair_id = ?",
                          [body.pair_id]).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "No such pair.")
    require_reauthentication(request)
    wait = app.limiter.hit("write", user.agent_id)
    if wait:
        raise ApiError(429, "rate_limited", f"Too many changes in a short time. Try again in {wait} s.",
                       {"Retry-After": str(wait)})
    record = {"node_a": row[0], "node_b": row[1], "decision": body.decision, "reviewer": user.agent_id,
              "decided_ts": utc_now().isoformat(timespec="seconds"), "evidence_key": row[2] or "",
              "note": clean_text(body.note)}
    s.decisions_csv.parent.mkdir(parents=True, exist_ok=True)
    with _CSV_LOCK:
        new = not s.decisions_csv.exists() or s.decisions_csv.stat().st_size == 0
        with open(s.decisions_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if new:
                w.writeheader()
            # keys are copied verbatim (the identity build matches on them); only the free-text note is escaped
            w.writerow({**record, "note": csv_safe(record["note"])})
    app.access_log.record(action=f"identity_decision:{body.decision}", user_id=user.agent_id, role=user.role,
                          object_id=body.pair_id, request_id=request.state.request_id)
    return {"ok": True, "decision": body.decision, "applies": "on the next identity run"}
