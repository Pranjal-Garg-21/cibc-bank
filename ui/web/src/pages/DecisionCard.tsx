import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/client'
import type { CaseResponse, Reasons } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, KeyValues, Loading, NotConnected, Notice, PageHeader, Section, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cad, cell, humanize, isoDate, pct } from '@/lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '@/lib/labels'

type Decision = 'accepted' | 'overridden' | 'approved' | 'rejected'

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
        <Link to={me.can.my_queue ? '/queue' : '/approvals'}>‹ Back to the queue</Link>
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
        title={`${label(ACTION_LABEL, rec.action)} — case ${rec.case_id}`}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>Recommendation {rec.recommendation_id}</span>
            <span>Business date {isoDate(rec.as_of_date)}</span>
            {state && <StatusBadge tone={STATE_TONE[state] ?? 'neutral'}>{label(STATE_LABEL, state)}</StatusBadge>}
            {rec.requires_approval && <StatusBadge tone="info">Needs approval: {label(APPROVAL_LABEL, rec.approval_reason)}</StatusBadge>}
          </span>
        }
        actions={
          <Button variant="secondary" className="no-print" onClick={() => window.print()}>
            Print
          </Button>
        }
      />
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
                ['Model version', rec.model_version ?? '—'],
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
          <Section title="Decision history" flush>
            <DataTable data={rec.history} empty="No decisions yet." caption="Decision history" />
          </Section>
        </div>
      </div>
    </div>
  )
}

/** The decision itself: accept / override for the assigned agent, approve / reject for an approver. */
function DecisionPanel({ data }: { data: CaseResponse }) {
  const me = useMe()
  const qc = useQueryClient()
  const rec = data.recommendation
  const reasons = useQuery({ queryKey: ['reasons'], queryFn: () => api.get<Reasons>('/api/review/reasons') })
  const [mode, setMode] = useState<'overridden' | 'rejected' | null>(null)
  const [reason, setReason] = useState('')
  const decide = useMutation({
    mutationFn: (d: { decision: Decision; reason_code?: string }) => api.post(`/api/review/${encodeURIComponent(rec.recommendation_id)}`, d),
    onSuccess: () => {
      setMode(null)
      setReason('')
      void qc.invalidateQueries({ queryKey: ['case', rec.recommendation_id] })
      void qc.invalidateQueries({ queryKey: ['queue'] })
      void qc.invalidateQueries({ queryKey: ['digest'] })
    },
  })
  const { agent_decision, approval } = data.allowed
  if (!agent_decision && !approval) return null
  const status = rec.status ?? {}
  const done = (v: unknown): boolean => !!v && !["pending", "not_needed"].includes(String(v))

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
                {mode === 'overridden' ? 'Reason for override' : 'Reason for rejection'}
              </Label>
              <Select value={reason} onValueChange={setReason}>
                <SelectTrigger id="reason" className="h-10 w-full rounded-sm">
                  <SelectValue placeholder="Select a reason" />
                </SelectTrigger>
                <SelectContent className="rounded-sm">
                  {reasons.data?.reasons.map((r) => (
                    <SelectItem key={r.code} value={r.code}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button type="submit" size="lg" disabled={!reason || decide.isPending}>
              {mode === 'overridden' ? 'Confirm override' : 'Confirm rejection'}
            </Button>
            <Button type="button" size="lg" variant="secondary" onClick={() => setMode(null)}>
              Cancel
            </Button>
          </form>
        ) : (
          <>
            {agent_decision && (
              <>
                <Button size="lg" disabled={decide.isPending} onClick={() => decide.mutate({ decision: 'accepted' })}>
                  Accept recommendation
                </Button>
                <Button size="lg" variant="outline" disabled={decide.isPending} onClick={() => setMode('overridden')}>
                  Override
                </Button>
              </>
            )}
            {approval && (
              <>
                <Button size="lg" disabled={decide.isPending} onClick={() => decide.mutate({ decision: 'approved' })}>
                  Approve
                </Button>
                <Button size="lg" variant="outline" disabled={decide.isPending} onClick={() => setMode('rejected')}>
                  Reject
                </Button>
              </>
            )}
            <span className="ml-auto text-[13px] text-muted-foreground">
              Recorded under your staff ID, {me.agent_id}.
              {agent_decision && done(status.agent) && ` Your current decision: ${String(status.agent)}.`}
              {approval && done(status.approval) && ` Current approval: ${String(status.approval)}.`}
            </span>
          </>
        )}
      </div>
      {decide.isSuccess && (
        <div className="mt-3">
          <Notice tone="ok">Your decision has been saved.</Notice>
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
