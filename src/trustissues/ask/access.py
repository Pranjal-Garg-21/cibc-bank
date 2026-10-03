"""Access decisions for Ask. Trusted code only: nothing here reads text from the question or the model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .data.schema_graph import SEMANTIC_JOIN_KEYS
from .data.semantic import SCHEMAS
from .data.validator import QueryPolicy
from .types import UserContext

# Which roles may use which path. Agents work single customers; portfolio aggregates are for leads and analysts.
PATH_ROLES = {
    "metric": {"team_lead", "analyst"},
    "sql": {"team_lead", "analyst"},
    "rag_corpus": {"agent", "team_lead", "analyst"},
    "rag_customer": {"agent", "team_lead"},
}


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class CaseOwner:
    golden_customer_id: str
    assigned_agent_id: str | None


class CaseResolver(Protocol):
    """Case id -> owner. The contract with P4 is pending; the Ask router only needs this one call."""
    def __call__(self, case_id: str) -> CaseOwner | None: ...


def check_path(user: UserContext, path: str) -> None:
    if user.role not in PATH_ROLES.get(path, set()):
        raise AccessDenied(f"Role '{user.role}' cannot use the {path} path.")


def check_case(user: UserContext, case_id: str, resolver: CaseResolver) -> CaseOwner:
    owner = resolver(case_id)
    if owner is None:
        raise AccessDenied("Case not found or not visible.")  # same wording for both: no existence oracle
    if user.role == "agent" and owner.assigned_agent_id != user.user_id:
        raise AccessDenied("Case not found or not visible.")
    return owner


def query_policy(user: UserContext, *, row_cap: int = 500) -> QueryPolicy:
    check_path(user, "metric")
    return QueryPolicy(user.role, SCHEMAS, join_keys=SEMANTIC_JOIN_KEYS, row_cap=row_cap)


def connection_resolver(con) -> Callable[[str], CaseOwner | None]:
    """Resolve cases from gold.semantic_cases on an already-open connection."""
    def resolve(case_id: str) -> CaseOwner | None:
        row = con.execute("SELECT golden_customer_id, assigned_agent_id FROM gold.semantic_cases WHERE case_id = ?",
                          [case_id]).fetchone()
        return CaseOwner(row[0], row[1]) if row else None
    return resolve
