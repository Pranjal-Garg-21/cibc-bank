import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { CaseResponse, Reasons } from '../api/types'
import { useMe } from '../auth'
import { Badge, Button, DataTable, ErrorState, FieldLabel, KeyValues, NotConnected, PageHeader, Panel, Spinner, cx, inputClass } from '../components/ui'
import { amount, cell, humanize, isoDate, pct } from '../lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '../lib/labels'

type Decision = 'accepted' | 'overridden' | 'approved' | 'rejected'

export function DecisionCard() {
  const me = useMe()
  const { recId = '' } = useParams()
  const q = useQuery({
    queryKey: ['case', recId],
    queryFn: () => api.get<CaseResponse>(`/api/cases/${encodeURIComponent(recId)}`),
    enabled: me.features.queue,
  })
  const back = me.can.my_queue ? '/queue' : '/approvals'

  return (
    <>
      <Link to={back} className="mb-3 inline-block text-sm text-accent-text hover:underline">
        ← Back to queue
      </Link>
      {!me.features.queue && <NotConnected feature="Decision cards" owner="P3" />}
      {q.isLoading && <Spinner />}
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
    <div className="pb-28">
      <PageHeader
        title={label(ACTION_LABEL, rec.action)}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs">{rec.recommendation_id}</span>
            <span>· Case {rec.case_id}</span>
            <span>· As of {isoDate(rec.as_of_date)}</span>
            {state && <Badge tone={STATE_TONE[state] ?? 'neutral'}>{label(STATE_LABEL, state)}</Badge>}
            {rec.requires_approval && <Badge tone="info">Needs approval: {label(APPROVAL_LABEL, rec.approval_reason)}</Badge>}
          </span>
        }
        actions={
          <Button variant="ghost" className="no-print" onClick={() => window.print()}>
            Print
          </Button>
        }
      />
      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <div className="space-y-6">
          <Panel title="Recommendation">
            {card.summary && <p className="text-[15px] leading-relaxed text-text">{card.summary}</p>}
            <div className="mt-4">
              <KeyValues
                items={[
                  ['Channel', cell(card.channel ?? rec.channel)],
                  ['Risk of not paying', pct(card.risk_score ?? rec.risk_score, 0)],
                  ['Expected value', `CAD ${amount(rec.expected_value)}`],
                  ...(card.contact_window ? [['Contact window', card.contact_window] as [string, string]] : []),
                  ...(card.runner_up_action ? [['Runner-up', label(ACTION_LABEL, card.runner_up_action)] as [string, string]] : []),
                  ['Model', <span className="font-mono text-xs">{rec.model_version ?? '—'}</span>],
                ]}
              />
            </div>
            {card.why_beat_runner_up && (
              <p className="mt-4 border-l-2 border-accent-text/60 pl-3 text-sm text-muted">{card.why_beat_runner_up}</p>
            )}
          </Panel>

          {!!card.top_reasons?.length && (
            <Panel title="Main reasons (model)">
              <ol className="space-y-2">
                {card.top_reasons.map((r, i) => (
                  <li key={r.feature} className="flex items-start gap-3 text-sm">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-raised text-xs text-muted">
                      {i + 1}
                    </span>
                    <span className="text-text">{r.text}</span>
                    <span className={cx('ml-auto font-mono text-xs', r.contribution > 0 ? 'text-warn' : 'text-ok')}>
                      {r.contribution > 0 ? 'raises risk' : 'lowers risk'}
                    </span>
                  </li>
                ))}
              </ol>
            </Panel>
          )}

          {!!card.blocked_actions?.length && (
            <Panel title="Actions ruled out">
              <DataTable
                data={card.blocked_actions.map((b) => ({ action: label(ACTION_LABEL, b.action), rule: humanize(b.rule) }))}
                caption="Actions ruled out"
                columns={[{ key: 'action', label: 'Action' }, { key: 'rule', label: 'Ruled out by' }]}
              />
            </Panel>
          )}

          {!!card.hardship_options?.length && (
            <Panel title="Hardship options">
              <DataTable data={card.hardship_options} caption="Hardship options" />
            </Panel>
          )}

          {!!card.evidence?.length && (
            <Panel title="Evidence from notes and calls (AI-extracted)">
              <ul className="space-y-2 text-sm">
                {card.evidence.map((e) => (
                  <li key={e.source_id} className="border-l-2 border-info/50 pl-3">
                    <p className="text-text">“{e.quote}”</p>
                    <p className="text-xs text-faint">
                      {e.source_id} · {isoDate(e.source_ts)}
                    </p>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>

        <div className="space-y-6">
          <Panel title="Case">
            <KeyValues items={Object.entries(data.context.facts).map(([k, v]) => [humanize(k), cell(v, k)])} />
          </Panel>
          <Panel title="Recent contacts">
            <DataTable data={data.context.contacts} empty="No recent contacts." caption="Recent contacts" />
          </Panel>
          <Panel title="Promises to pay">
            <DataTable data={data.context.promises} empty="No promises on record." caption="Promises to pay" />
          </Panel>
          <Panel title="Decision history">
            <DataTable data={rec.history} empty="No decisions yet." caption="Decision history" />
          </Panel>
        </div>
      </div>
      <ActionBar data={data} />
    </div>
  )
}

function ActionBar({ data }: { data: CaseResponse }) {
  const qc = useQueryClient()
  const rec = data.recommendation
  const reasons = useQuery({ queryKey: ['reasons'], queryFn: () => api.get<Reasons>('/api/review/reasons') })
  const [mode, setMode] = useState<'overridden' | 'rejected' | null>(null)
  const [reason, setReason] = useState('')
  const decide = useMutation({
    mutationFn: (d: { decision: Decision; reason_code?: string }) =>
      api.post(`/api/review/${encodeURIComponent(rec.recommendation_id)}`, d),
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
  const agentDone = status.agent && status.agent !== 'pending' && status.agent !== 'not_needed'
  const approvalDone = status.approval && status.approval !== 'pending' && status.approval !== 'not_needed'

  return (
    <div className="no-print fixed right-0 bottom-0 left-60 border-t border-border-strong bg-raised/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-end gap-3 px-6 py-3">
        {mode ? (
          <form
            className="flex flex-1 flex-wrap items-end gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              if (reason) decide.mutate({ decision: mode, reason_code: reason })
            }}
          >
            <div className="min-w-72">
              <FieldLabel htmlFor="reason">{mode === 'overridden' ? 'Override reason' : 'Rejection reason'} (required)</FieldLabel>
              <select id="reason" className={inputClass} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus>
                <option value="">Choose a reason…</option>
                {reasons.data?.reasons.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" variant="danger" disabled={!reason || decide.isPending}>
              {mode === 'overridden' ? 'Confirm override' : 'Confirm rejection'}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setMode(null)}>
              Cancel
            </Button>
          </form>
        ) : (
          <div className="flex flex-1 flex-wrap items-center gap-3">
            {agent_decision && (
              <>
                <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate({ decision: 'accepted' })}>
                  Accept
                </Button>
                <Button variant="secondary" disabled={decide.isPending} onClick={() => setMode('overridden')}>
                  Override…
                </Button>
                {agentDone && <span className="text-xs text-muted">Your decision: {String(status.agent)} (a new one replaces it)</span>}
              </>
            )}
            {approval && (
              <>
                <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate({ decision: 'approved' })}>
                  Approve
                </Button>
                <Button variant="danger" disabled={decide.isPending} onClick={() => setMode('rejected')}>
                  Reject…
                </Button>
                {approvalDone && <span className="text-xs text-muted">Approval: {String(status.approval)}</span>}
              </>
            )}
          </div>
        )}
        {decide.error && (
          <div className="w-full">
            <ErrorState error={decide.error} />
          </div>
        )}
        {decide.isSuccess && <span className="text-sm text-ok" role="status">Saved.</span>}
      </div>
    </div>
  )
}
