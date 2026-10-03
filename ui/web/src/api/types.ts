// Response shapes of the UI API (ui/tiui/routers). Kept by hand; small enough to read.

export type Table = { columns: string[]; rows: unknown[][] }

export type Me = {
  agent_id: string
  name: string | null
  role: string
  team_id: string | null
  certified: boolean
  ask_role: string
  csrf_token: string
  as_of_date: string
  idle_minutes: number
  demo: boolean
  site: string | null
  team_lead_id: string | null
  languages: string | null
  employment_type: string | null
  signed_in_at: string
  max_hours: number
  features: { queue: boolean; ask: boolean; identity: boolean; status: boolean }
  can: { ask: boolean; my_queue: boolean; approvals: boolean; identity_review: boolean; status: boolean }
}

export type Options = { sign_in_configured: boolean; demo: boolean; as_of_date: string; idle_minutes: number }

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
