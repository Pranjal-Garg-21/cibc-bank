// Response shapes of the UI API (ui/tiui/routers). Kept by hand; small enough to read.

export type Table = { columns: string[]; rows: unknown[][] }
export type WarehouseCatalog = { tables: { schema: string; name: string; rows: number }[] }
export type WarehouseTable = {
  schema: string; name: string; columns: { name: string; type: string; masked: boolean }[]
  total: number; offset: number; limit: number; data: Table
}

export type Me = {
  agent_id: string
  name: string | null
  role: string
  team_id: string | null
  certified: boolean
  ask_role: string
  csrf_token: string
  as_of_date: string
  idle_minutes: number | null
  demo: boolean
  shared_access: boolean
  site: string | null
  team_lead_id: string | null
  languages: string | null
  employment_type: string | null
  signed_in_at: string
  max_hours: number | null
  mfa: boolean
  transport_secure: boolean
  features: { queue: boolean; ask: boolean; identity: boolean; status: boolean }
  can: Record<
    'overview' | 'my_queue' | 'next_action' | 'case_view' | 'decide' | 'approvals' | 'approve_batch' | 'manage_agents' |
    'identity_review' | 'decision_review' | 'status' | 'audit_log' | 'tables' | 'ask' | 'ask_case' | 'ask_portfolio',
    boolean
  >
  approval_kinds: string[]
}

export type TeamAgent = {
  agent_id: string
  name: string | null
  role: string
  team_id: string | null
  has_account: boolean
  mfa_enrolled: boolean
  set_at: string | null
  set_by: string | null
}
export type TeamAgents = { team_lead: string; agents: TeamAgent[] }

export type Options = { sign_in_configured: boolean; demo: boolean; as_of_date: string; idle_minutes: number | null; mfa_required: boolean; shared_access: boolean }
export type SessionState = { idle_seconds_left: number | null; absolute_seconds_left: number | null }
export type SecurityInfo = {
  sessions: { id: string; current: boolean; device: string; signed_in_at: string; mfa: boolean }[]
  events: { at: string; action: string; outcome: string }[]
  max_sessions: number
}

export type AnswerCard = {
  question: string
  answer: string
  path: 'refused' | 'metric' | 'sql' | 'rag_customer' | 'rag_corpus' | 'cannot_answer' | 'drivers'
  as_of_date: string
  refused: boolean
  refusal_reason: string | null
  metric_id: string | null
  sql: string | null
  parameters: Record<string, unknown>
  certified: boolean
  columns: string[]
  rows: unknown[][]
  truncated: boolean
  sources: string[]
  notes: string[]
}

export type QueueResponse = { as_of_date: string; queue: Table }

export type Digest = {
  approval_kinds?: string[]
  as_of_date: string
  states: Record<string, number>
  batch_pending: number
  batch_total: number
  pending: Table
}

// gold.nba_decisions.decision_card (common/interfaces.py DecisionCard, plus P3's extra fields)
export type DecisionCardPayload = {
  recommended_action?: string
  channel?: string | null
  risk_score?: number
  top_reasons?: { feature: string; text: string; contribution: number }[]
  runner_up_action?: string | null
  why_beat_runner_up?: string | null
  blocked_actions?: { action: string; rule: string }[]
  evidence?: { source_id: string; source_ts: string; quote: string }[]
  requires_approval?: boolean
  approval_reason?: string | null
  summary?: string
  support_reason?: string | null
  hardship_options?: Record<string, unknown>[]
  contact_window?: string | null
  [k: string]: unknown
}

export type CaseResponse = {
  recommendation: {
    recommendation_id: string
    case_id: string
    agent_id: string | null
    action: string
    channel: string | null
    risk_score: number | null
    expected_value: number | null
    requires_approval: boolean
    approval_reason: string | null
    as_of_date: string
    model_version: string | null
    card: DecisionCardPayload
    status: { approval?: string; agent?: string; state?: string; [k: string]: unknown }
    history: Table
    [k: string]: unknown
  }
  context: { facts: Record<string, unknown>; contacts: Table; promises: Table }
  customer: { customer: string | null; cases: Table; offers: Table }
  allowed: { agent_decision: boolean; approval: boolean }
}

export type Reasons = { reasons: { code: string; label: string }[]; safeguard_reasons: string[] }

export type ReviewSummary = {
  from: string
  to: string
  recommendations: number
  agent_decisions: number
  override_rate: number | null
  overrides_by_reason: Record<string, number>
  overrides_by_action: Record<string, number>
  rejections_by_reason: Record<string, number>
  still_pending: Record<string, number>
  [k: string]: unknown
}

export type IdentityItem = {
  pair_id: string
  reason: string
  source_a: string
  source_b: string
  splink_probability: number | null
  llm_match: boolean | null
  llm_available: boolean
  decision: string | null
}
export type IdentityQueue = { total: number; counts_by_reason: Record<string, number>; items: IdentityItem[] }
export type RecordField = { field: string; label: string; value: string }
export type IdentityPair = {
  pair_id: string
  reason: string
  decision: string | null
  record_a: RecordField[]
  record_b: RecordField[]
  splink_probability: number | null
  splink_explanation: string[]
  llm: { available: boolean; match: boolean | null; probability: number | null; reason: string | null }
  detail: string | null
}

export type Status = {
  as_of_date: string
  publish: { attempt_ts: string; published: boolean; failed_rules: string; warned_rules: string; secs: number } | null
  rules: { rule_id: string; title: string; blocking: boolean; violations: number; total: number; passed: boolean }[]
  row_counts: Record<string, number>
  reports: { name: string; title: string; modified: string }[]
}

// /api/next-action: gold.nba_decisions by customer
export type NextActionSearch = {
  as_of_date: string
  customers: { customer_id: string; cases: number; action: string; channel: string | null }[]
}
export type NextActionRecommendation = {
  recommendation_id: string
  case_id: string
  agent_id: string | null
  as_of_date: string
  action: string
  channel: string | null
  risk_score: number | null
  expected_value: number | null
  requires_approval: boolean
  approval_reason: string | null
  model_version: string | null
  card: DecisionCardPayload
}
export type NextActionCustomer = { as_of_date: string; customer_id: string; recommendations: NextActionRecommendation[] }
export type Freshness = { ok: boolean; refreshed_at: string | null; message: string | null }

// /api/overview: a page described by the server for the signed-in role (tiles and sections)
export type OverviewTile = { id: string; label: string; value: number | null; note: string | null; tone: 'ok' | 'warn' | 'danger' | null; to: string | null; kind: 'percent' | null }
export type OverviewColumn = { key: string; label: string; kind?: 'percent' | 'money' | 'int'; wrap?: boolean }
export type OverviewSection =
  | { id: string; title: string; description: string | null; kind: 'bars'; items: { label: string; value: number; to: string | null }[] }
  | { id: string; title: string; description: string | null; kind: 'table'; columns: OverviewColumn[]; rows: Record<string, unknown>[]; empty: string }
export type Overview = { as_of_date: string; role: string; headline: string; intro: string; tiles: OverviewTile[]; sections: OverviewSection[] }

// /api/oversight
export type DecisionReviewData = {
  from: string; to: string; days: number; scope: 'team' | 'bank'
  totals: { decisions: number; accepted: number; overridden: number; override_rate: number | null; approved: number; rejected: number }
  by_reason: { code: string; label: string; n: number; safeguard: boolean }[]
  rejections: { code: string; label: string; n: number }[]
  by_agent: { agent_id: string; name: string | null; decisions: number; overrides: number; rate: number; top_reason: string | null; outlier: boolean }[]
  by_day: { day: string; decisions: number; overrides: number }[]
}
export type AuditEvent = { at: string; user_id: string | null; role: string | null; action: string; object_id: string; outcome: string; request_id: string | null }
export type AuditData = { hours: number; events: AuditEvent[]; total: number; issues: number; actions: string[]; outcomes: string[] }

export type AskExamples = { examples: string[]; llm_enabled: boolean; model_configured: boolean; policy_index: boolean }
