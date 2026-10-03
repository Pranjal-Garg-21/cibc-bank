// Labels shared with the Streamlit app (app/ui.py) so both interfaces say the same thing.

export const ACTION_LABEL: Record<string, string> = {
  support_call: 'Support call',
  agent_call: 'Call',
  sms_reminder: 'SMS reminder',
  email_reminder: 'Email reminder',
  push_reminder: 'Push reminder',
  letter: 'Letter',
  wait: 'Wait',
}

export const STATE_LABEL: Record<string, string> = {
  ready: 'Ready',
  waiting_agent: 'Agent to decide',
  waiting_approval: 'To approve',
  blocked: 'Rejected',
  overridden: 'Overridden',
}

export type Tone = 'neutral' | 'ok' | 'warn' | 'danger' | 'info' | 'accent'

export const STATE_TONE: Record<string, Tone> = {
  ready: 'ok',
  waiting_agent: 'warn',
  waiting_approval: 'warn',
  blocked: 'danger',
  overridden: 'neutral',
}

export const ROLE_LABEL: Record<string, string> = {
  agent: 'Agent',
  senior_agent: 'Senior agent',
  team_lead: 'Team lead',
  hardship_specialist: 'Hardship specialist',
  qa_analyst: 'QA analyst',
}

export const APPROVAL_LABEL: Record<string, string> = {
  vulnerability: 'Vulnerability',
  hardship: 'Hardship',
  high_balance: 'High balance',
  digital_batch: 'Digital batch',
}

export const ANSWER_PATH: Record<string, { label: string; tone: Tone; help: string }> = {
  metric: { label: 'Certified metric', tone: 'ok', help: 'Answered from a reviewed metric definition.' },
  drivers: { label: 'Certified metric · drivers', tone: 'ok', help: 'Breakdown of a reviewed metric.' },
  sql: {
    label: 'Generated query',
    tone: 'warn',
    help: 'A query written for this question and checked by the validator, not a reviewed metric.',
  },
  rag_corpus: { label: 'Policy answer · AI', tone: 'info', help: 'Written by a language model from policy documents.' },
  rag_customer: { label: 'Customer answer · AI', tone: 'info', help: 'Written by a language model from case records.' },
  refused: { label: 'Refused', tone: 'neutral', help: 'This question is outside what the assistant may answer.' },
  cannot_answer: { label: 'Not answered', tone: 'neutral', help: 'The assistant could not answer reliably.' },
}

export const IDENTITY_REASON: Record<string, string> = {
  ambiguous: 'Ambiguous rule match',
  splink_ambiguous: 'Probabilistic match unclear',
  grey_zone_no_llm: 'Grey zone, no AI opinion',
  blocked_conflict: 'Blocked by a conflict',
  cluster_conflict: 'Cluster conflict',
}

export function label(map: Record<string, string>, key: string | null | undefined): string {
  if (!key) return '—'
  return map[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}
