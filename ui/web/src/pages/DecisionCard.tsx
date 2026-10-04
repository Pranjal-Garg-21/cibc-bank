import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, DECISION_QUERIES } from '@/api/client'
import type { CaseResponse, Reasons } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, KeyValues, Loading, NotConnected, Notice, PageHeader, Section, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cad, cell, dateTime, humanize, isoDate, pct } from '@/lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '@/lib/labels'

const AGENT_DECISION_LABEL: Record<string, string> = { accepted: 'Accepted', overridden: 'Overridden' }
const APPROVAL_DECISION_LABEL: Record<string, string> = { approved: 'Approved', rejected: 'Rejected' }

type Decision = 'accepted' | 'overridden' | 'approved' | 'rejected' | 'completed'

export function DecisionCard() {
  const me = useMe()
  const { recId = '' } = useParams()
  const q = useQuery({
    queryKey: ['case', recId],
    queryFn: () => api.get<CaseResponse>(`/api/cases/${encodeURIComponent(recId)}`),
    enabled: me.features.queue,
  })
  return (
    <>
      <p className="mb-3 text-sm">
        <Link to={me.can.my_queue ? '/queue' : me.can.approvals ? '/approvals' : '/next-action'}>‹ {me.can.my_queue ? 'Back to the queue' : me.can.approvals ? 'Back to approvals' : 'Back'}</Link>
      </p>
      {!me.features.queue && <NotConnected feature="Decision cards" owner="P3" />}
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && <CardView data={q.data} />}
    </>
  )
}

function CardView({ data }: { data: CaseResponse }) {
  const rec = data.recommendation
  const card = rec.card ?? {}
  const state = rec.status?.state as string | undefined
  return (
    <div className="pb-6">
      <PageHeader
        title={`${label(ACTION_LABEL, rec.action)} - case ${rec.case_id}`}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>Recommendation {rec.recommendation_id}</span>
            {!!data.customer?.customer && <span>Customer {data.customer.customer}</span>}
            {!!rec.feature_snapshot_id && <span>Feature snapshot {String(rec.feature_snapshot_id)}</span>}
            <span>Business date {isoDate(rec.as_of_date)}</span>
            {state && <StatusBadge tone={STATE_TONE[state] ?? 'neutral'}>{label(STATE_LABEL, state)}</StatusBadge>}
            {rec.requires_approval && (rec.status?.approval === 'approved' ? <StatusBadge tone="ok">Approved: {label(APPROVAL_LABEL, rec.approval_reason)}</StatusBadge>
              : rec.status?.approval === 'rejected' ? <StatusBadge tone="danger">Rejected at approval: {label(APPROVAL_LABEL, rec.approval_reason)}</StatusBadge>
              : rec.status?.agent === 'overridden' ? null
              : <StatusBadge tone="info">Needs approval: {label(APPROVAL_LABEL, rec.approval_reason)}</StatusBadge>)}
            {!rec.requires_approval && <StatusBadge tone="neutral">{rec.action === 'wait' ? 'No contact today' : 'No approval needed'}</StatusBadge>}
            {(card.support_reason || !!card.hardship_options?.length) && <StatusBadge tone="warn">Hardship / vulnerable</StatusBadge>}
          </span>
        }
        actions={
          <Button variant="secondary" className="no-print" onClick={() => window.print()}>
            Print
          </Button>
        }
      />
      {data.override && (
        <div className="no-print mb-4">
          <Notice tone="warn" title={`Overridden by ${data.override.name ? `${data.override.name} (${data.override.by})` : data.override.by}`}>
            Reason: {data.override.reason}. Recorded {dateTime(data.override.at)}. Nothing is left to approve, and it will not be carried out today.
          </Notice>
        </div>
      )}
      <DecisionPanel data={data} />
      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="space-y-5">
          <Section title="Recommendation">
            {card.summary && <p className="mb-3 text-[15px] leading-relaxed">{card.summary}</p>}
            <KeyValues
              columns={2}
              items={[
                ['Channel', cell(card.channel ?? rec.channel)],
                ['Risk of non-payment', pct(card.risk_score ?? rec.risk_score, 0)],
                ['Expected value', cad(rec.expected_value)],
                ['Next-best alternative', label(ACTION_LABEL, card.runner_up_action ?? null)],
                ...(card.contact_window ? [['Contact window', card.contact_window] as [string, string]] : []),
                ['Model version', rec.model_version ?? '-'],
              ]}
            />
            {card.why_beat_runner_up && <p className="mt-3 text-sm text-muted-foreground">{card.why_beat_runner_up}</p>}
          </Section>

          {!!card.top_reasons?.length && (
            <Section title="Main reasons from the risk model">
              <ol className="list-decimal space-y-1.5 pl-5 text-sm">
                {card.top_reasons.map((r) => (
                  <li key={r.feature}>
                    {r.text} <span className="text-muted-foreground">({r.contribution > 0 ? 'raises' : 'lowers'} risk)</span>
                  </li>
                ))}
              </ol>
            </Section>
          )}

          {!!card.blocked_actions?.length && (
            <Section title="Actions ruled out by policy" flush>
              <DataTable
                data={card.blocked_actions.map((b) => ({ action: label(ACTION_LABEL, b.action), rule: humanize(b.rule) }))}
                caption="Actions ruled out"
                columns={[{ key: 'action', label: 'Action' }, { key: 'rule', label: 'Rule' }]}
              />
            </Section>
          )}

          {!!card.hardship_options?.length && (
            <Section title="Hardship options" flush>
              <DataTable data={card.hardship_options} caption="Hardship options" />
            </Section>
          )}

          {!card.evidence?.length && (
            <Section title="From notes and calls">
              <p className="text-sm text-muted-foreground">No quoted signal from this customer's notes or calls in the last 90 days.</p>
            </Section>
          )}
          {!!card.evidence?.length && (
            <Section title="From notes and calls" description="Extracted automatically; check before relying on it">
              <ul className="space-y-3 text-sm">
                {card.evidence.map((e) => (
                  <li key={e.source_id} className="border-l-4 border-input pl-3">
                    <p>“{e.quote}”</p>
                    <p className="text-xs text-muted-foreground">
                      {e.source_id}, {isoDate(e.source_ts)}
                    </p>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>

        <div className="space-y-5">
          <Section title="Account and case">
            <KeyValues items={Object.entries(data.context.facts).map(([k, v]) => [humanize(k), cell(v, k)])} />
          </Section>
          <Section title="Recent contacts" flush>
            <DataTable data={data.context.contacts} empty="No recent contacts." caption="Recent contacts" />
          </Section>
          <Section title="Promises to pay" flush>
            <DataTable data={data.context.promises} empty="No promises on record." caption="Promises to pay" />
          </Section>
          {data.customer && (
            <Section
              title="The customer's cases"
              description="One person across their CRM, card, loan and deposit records. Safeguards, the 7-day contact limit and hardship history are theirs, across all these cases; they are contacted on one case a day at most."
              flush
            >
              <DataTable data={data.customer.cases} empty="No other cases." caption="The customer's cases" />
            </Section>
          )}
          {data.customer && (
            <Section title="Hardship offers on any of their cases" flush>
              <DataTable data={data.customer.offers} empty="No hardship offers." caption="Hardship offers" />
            </Section>
          )}
          <Section title="Decision history" flush>
            <DataTable data={rec.history} empty="No decisions yet." caption="Decision history" />
          </Section>
        </div>
      </div>
    </div>
  )
}

/** The decision itself: accept / override for the assigned agent, mark complete once a call is cleared (with its
 * outcome), approve / reject for an approver. A completed call or an accepted wait leaves the day's queue (Done). */
function DecisionPanel({ data }: { data: CaseResponse }) {
  const me = useMe()
  const qc = useQueryClient()
  const rec = data.recommendation
  const reasons = useQuery({ queryKey: ['reasons'], queryFn: () => api.get<Reasons>('/api/review/reasons') })
  const [mode, setMode] = useState<'overridden' | 'rejected' | 'completed' | null>(null)
  const [reason, setReason] = useState('')
  const decide = useMutation({
    mutationFn: (d: { decision: Decision; reason_code?: string }) => api.post(`/api/review/${encodeURIComponent(rec.recommendation_id)}`, d),
    onSuccess: () => {
      setMode(null)
      setReason('')
      // everything this decision can change, so other screens in this browser are current at once
      DECISION_QUERIES.forEach((queryKey) => void qc.invalidateQueries({ queryKey }))
    },
  })
  const { agent_decision, approval, complete } = data.allowed
  const status = rec.status ?? {}
  const outcomeLabel = (code: unknown) => reasons.data?.call_outcomes.find((o) => o.code === code)?.label ?? humanize(String(code ?? ''))
  if (!agent_decision && !approval && !complete) {
    return status.completion === 'completed' ? (
      <section className="no-print border border-border border-t-4 border-t-brand bg-white p-4">
        <Notice tone="ok">Call made and marked complete: {outcomeLabel(status.outcome)}. Nothing more to decide on this case.</Notice>
      </section>
    ) : null
  }
  // nothing is done twice (D39): the button for the decision already in place is greyed out
  const accepted = status.agent === 'accepted'
  const overridden = status.agent === 'overridden'
  const approvalDone = status.approval === 'approved' || status.approval === 'rejected'
  const options = mode === 'completed' ? reasons.data?.call_outcomes : reasons.data?.reasons
  const saved = decide.variables?.decision
  const leftQueue = saved === 'completed' || (saved === 'accepted' && rec.action === 'wait')

  return (
    <section className="no-print border border-border border-t-4 border-t-brand bg-white p-4">
      <div className="flex flex-wrap items-end gap-3">
        {mode ? (
          <form
            className="flex flex-1 flex-wrap items-end gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              if (reason) decide.mutate({ decision: mode, reason_code: reason })
            }}
          >
            <div className="w-full max-w-md">
              <Label htmlFor="reason" className="mb-1.5 text-[14px] font-bold">
                {mode === 'overridden' ? 'Reason for override' : mode === 'completed' ? 'Call outcome' : 'Reason for rejection'}
              </Label>
              <Select value={reason} onValueChange={setReason}>
                <SelectTrigger id="reason" className="h-10 w-full rounded-sm">
                  <SelectValue placeholder={mode === 'completed' ? 'Select what happened' : 'Select a reason'} />
                </SelectTrigger>
                <SelectContent className="rounded-sm">
                  {options?.map((r) => (
                    <SelectItem key={r.code} value={r.code}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button type="submit" size="lg" disabled={!reason || decide.isPending}>
              {mode === 'overridden' ? 'Confirm override' : mode === 'completed' ? 'Mark as complete' : 'Confirm rejection'}
            </Button>
            <Button type="button" size="lg" variant="secondary" onClick={() => { setMode(null); setReason('') }}>
              Cancel
            </Button>
          </form>
        ) : (
          <>
            {complete && (
              <Button size="lg" disabled={decide.isPending} onClick={() => setMode('completed')}>
                Mark as complete
              </Button>
            )}
            {agent_decision && (
              <>
                <Button size="lg" variant={complete ? 'outline' : 'default'} disabled={decide.isPending || accepted} aria-describedby="current-decision"
                  title={accepted ? 'Already accepted. Override is the only change left.' : undefined}
                  onClick={() => decide.mutate({ decision: 'accepted' })}>
                  {accepted ? 'Accepted' : 'Accept recommendation'}
                </Button>
                <Button size="lg" variant="outline" disabled={decide.isPending || overridden} aria-describedby="current-decision"
                  title={overridden ? 'Already overridden.' : undefined} onClick={() => setMode('overridden')}>
                  {overridden ? 'Overridden' : 'Override'}
                </Button>
              </>
            )}
            {approval && (
              <>
                <Button size="lg" disabled={decide.isPending || approvalDone} aria-describedby="current-decision"
                  title={approvalDone ? 'An approval decision is final.' : undefined} onClick={() => decide.mutate({ decision: 'approved' })}>
                  {status.approval === 'approved' ? 'Approved' : 'Approve'}
                </Button>
                <Button size="lg" variant="outline" disabled={decide.isPending || approvalDone} aria-describedby="current-decision"
                  title={approvalDone ? 'An approval decision is final.' : undefined} onClick={() => setMode('rejected')}>
                  {status.approval === 'rejected' ? 'Rejected' : 'Reject'}
                </Button>
              </>
            )}
            <span id="current-decision" className="ml-auto flex flex-wrap items-center justify-end gap-2 text-[13px] text-muted-foreground">
              {agent_decision && (accepted || overridden) && (
                <span>Your current decision: <StatusBadge tone={accepted ? 'ok' : 'neutral'}>{AGENT_DECISION_LABEL[String(status.agent)]}</StatusBadge>
                  {accepted && ' Override is the only way to change it.'}</span>
              )}
              {approval && approvalDone && (
                <span>Approval: <StatusBadge tone={status.approval === 'approved' ? 'ok' : 'danger'}>{APPROVAL_DECISION_LABEL[String(status.approval)]}</StatusBadge> (final)</span>
              )}
              <span>Recorded under your staff ID, {me.agent_id}.</span>
            </span>
          </>
        )}
      </div>
      {decide.isSuccess && (
        <div className="mt-3">
          <Notice tone="ok">
            {saved === 'completed' ? 'Call marked complete. ' : 'Your decision has been saved. '}
            {leftQueue && (
              <>
                It has moved to Done and left today's queue. <Link to="/queue">Back to the queue</Link>
              </>
            )}
          </Notice>
        </div>
      )}
      {decide.error && (
        <div className="mt-3">
          <ErrorState error={decide.error} />
        </div>
      )}
    </section>
  )
}
