"""Fit the day's decisions into what can actually be done that day.

Capacity on the as-of date:
- channel_capacity: outbound call attempts, SMS, emails, pushes and letters the bank can send
- agent_shifts: each working agent's outbound_call_capacity (absent agents have none)

Cases are taken most valuable first. Each gets its best action that still fits; an action that does not fit
is recorded as blocked by `no_capacity` and the case moves to its next best (in the end, `wait`, which
always fits). A call goes to the case's assigned agent if they are working and have room, otherwise to
the agent in the same team with the most room, otherwise to any working agent with room. Cases routed for
hardship or vulnerability only go to certified agents.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from trustissues.nba.actions import CAPACITY_CHANNEL, CATALOGUE


@dataclass
class Capacity:
    channel: dict[str, float]  # remaining units per channel (team names: phone, sms, email, push, mail)
    agent_calls: dict[str, int]  # remaining outbound calls per working agent
    team: dict[str, str]  # agent -> team
    certified: set[str]  # agents certified for vulnerable customers
    by_team: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self):
        teams = defaultdict(list)
        for a in self.agent_calls:
            teams[self.team.get(a)].append(a)
        self.by_team = dict(teams)


@dataclass
class Request:
    """One case's options, best first, plus what routing needs."""
    order: list[str]  # candidate actions, best first; ends with wait
    value: float  # value of the best candidate (sets the processing order)
    assigned_agent: str | None
    team: str | None
    needs_certified: bool


@dataclass
class Assignment:
    action: str
    agent_id: str | None
    no_capacity: list[str]  # better actions that did not fit


def load_capacity(con, as_of) -> Capacity:
    from trustissues.features import sources

    channel = {CAPACITY_CHANNEL.get(ch, ch): units for ch, units in sources.channel_capacity(con).filter(
        f"date = DATE '{as_of}'").aggregate("channel, sum(capacity_units) - coalesce(sum(used_units), 0)").fetchall()}
    calls = dict(sources.agent_shifts(con).filter(
        f"shift_date = DATE '{as_of}' AND NOT coalesce(absence_flag, false)").aggregate(
        "agent_id, sum(outbound_call_capacity)").fetchall())
    agents = sources.agents(con).filter("status = 'active'").select(
        "agent_id, team_id, coalesce(vulnerable_customer_certified, false)").fetchall()
    calls = {a: int(n or 0) for a, n in calls.items() if a in {r[0] for r in agents}}
    return Capacity(channel=channel, agent_calls=calls, team={a: t for a, t, _ in agents},
                    certified={a for a, _, c in agents if c})


def _pick_agent(req: Request, cap: Capacity) -> str | None:
    def ok(a):
        return cap.agent_calls.get(a, 0) > 0 and (not req.needs_certified or a in cap.certified)

    if req.assigned_agent and ok(req.assigned_agent):
        return req.assigned_agent
    for pool in (cap.by_team.get(req.team, []), cap.agent_calls):
        best = max((a for a in pool if ok(a)), key=lambda a: cap.agent_calls[a], default=None)
        if best:
            return best
    return None


def allocate(requests: list[Request], cap: Capacity) -> list[Assignment]:
    out: list[Assignment | None] = [None] * len(requests)
    for i in sorted(range(len(requests)), key=lambda i: -requests[i].value):
        req = requests[i]
        skipped = []
        for action in req.order:
            channel = CATALOGUE[action].channel
            if channel is None:
                out[i] = Assignment(action, req.assigned_agent, skipped)
                break
            if cap.channel.get(channel, float("inf")) < 1:
                skipped.append(action)
                continue
            agent = None
            if channel == "phone":
                agent = _pick_agent(req, cap)
                if agent is None:
                    skipped.append(action)
                    continue
                cap.agent_calls[agent] -= 1
            cap.channel[channel] = cap.channel.get(channel, float("inf")) - 1
            out[i] = Assignment(action, agent, skipped)
            break
        else:  # order did not end with wait
            out[i] = Assignment("wait", req.assigned_agent, skipped)
    return out
