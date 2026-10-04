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

export const CHANNEL_LABEL: Record<string, string> = {
  phone: 'Phone call',
  sms: 'SMS',
  email: 'Email',
  mail: 'Letter by post',
  push: 'App push notification',
}

export const SUPPORT_LABEL: Record<string, string> = {
  offer_programme: 'Offer a hardship programme',
  plan_ending: 'Hardship plan ending soon',
  follow_up: 'Follow up on a hardship offer',
  vulnerable_contact: 'Vulnerable customer due for contact',
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

/** What each role does, in the bank's words (shown on the profile and the sign-in list). */
export const ROLE_DUTY: Record<string, string> = {
  agent: 'Works assigned collections cases; accepts or overrides recommended contacts.',
  senior_agent: 'Works complex collections cases; accepts or overrides recommended contacts.',
  team_lead: "Approves high-balance, hardship and vulnerability cases and the daily digital batch; manages the team's agent accounts and reviews customer identity matches.",
  hardship_specialist: 'Approves hardship and vulnerability cases; handles payment arrangements.',
  qa_analyst: 'Reviews decisions and data quality; resolves customer identity matches.',
}

export const EMPLOYMENT_LABEL: Record<string, string> = {
  employee: 'Bank employee',
  vendor: 'Vendor (outsourced)',
}

const LANGUAGE_NAMES = new Intl.DisplayNames(['en-CA'], { type: 'language' })

/** '["EN", "FR"]' (as stored) -> 'English, French'. */
export function languages(v: string | null | undefined): string {
  if (!v) return '-'
  return v
    .replace(/[[\]"']/g, '')
    .split(/[|,;/\s]+/)
    .filter(Boolean)
    .map((code) => {
      try {
        return LANGUAGE_NAMES.of(code.toLowerCase()) ?? code
      } catch {
        return code
      }
    })
    .join(', ')
}

export function initials(name: string | null | undefined, fallback: string): string {
  const parts = (name ?? '').replace(/[^A-Za-z\s.-]/g, '').split(/[\s.]+/).filter(Boolean)
  return (parts.length ? parts.slice(0, 2).map((p) => p[0]) : [fallback.slice(-2)]).join('').toUpperCase()
}

export const APPROVAL_LABEL: Record<string, string> = {
  vulnerability: 'Vulnerability',
  hardship: 'Hardship',
  high_balance: 'High balance',
  digital_batch: 'Digital batch',
}

export const ANSWER_PATH: Record<string, { label: string; tone: Tone; help: string }> = {
  metric: { label: 'Certified metric', tone: 'ok', help: 'Answered from a reviewed metric definition.' },
  drivers: { label: 'Certified metric, breakdown', tone: 'ok', help: 'Breakdown of a reviewed metric.' },
  sql: {
    label: 'Generated query',
    tone: 'warn',
    help: 'A query written for this question and checked by the validator, not a reviewed metric.',
  },
  rag_corpus: { label: 'Policy answer (generated)', tone: 'info', help: 'Written by a language model from policy documents.' },
  rag_customer: { label: 'Case answer (generated)', tone: 'info', help: 'Written by a language model from case records.' },
  case_facts: { label: 'Case facts', tone: 'ok', help: "Fixed facts read from the case records and today's queue. No language model." },
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
  if (!key) return '-'
  return map[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}
