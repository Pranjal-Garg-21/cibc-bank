import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Digest, Reasons, ReviewSummary } from '../api/types'
import { useMe } from '../auth'
import { Badge, Button, DataTable, Dialog, ErrorState, NotConnected, PageHeader, Panel, Spinner, Stat } from '../components/ui'
import { int, isoDate, pct } from '../lib/format'
import { APPROVAL_LABEL, STATE_LABEL, label } from '../lib/labels'

export function Approvals() {
  const me = useMe()
  const nav = useNavigate()
  const qc = useQueryClient()
  const digest = useQuery({ queryKey: ['digest'], queryFn: () => api.get<Digest>('/api/queue/digest'), enabled: me.features.queue })
  const summary = useQuery({
    queryKey: ['summary'],
    queryFn: () => api.get<ReviewSummary>('/api/review/summary?days=7'),
    enabled: me.features.queue,
  })
  const [confirm, setConfirm] = useState(false)
  const batch = useMutation({
    mutationFn: () => api.post<{ result: unknown }>('/api/review-batch', { rejected: {} }),
    onSuccess: () => {
      setConfirm(false)
      void qc.invalidateQueries({ queryKey: ['digest'] })
      void qc.invalidateQueries({ queryKey: ['summary'] })
    },
  })
  const isLead = me.role === 'team_lead'
  const d = digest.data

  return (
    <>
      <PageHeader title="Team & approvals" subtitle="Where today's recommendations stand, and the approvals waiting on you." />
      {!me.features.queue && <NotConnected feature="Approvals" owner="P3" />}
      {digest.isLoading && <Spinner />}
      {digest.error && <ErrorState error={digest.error} onRetry={() => digest.refetch()} />}
      {d && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {Object.entries(STATE_LABEL).map(([k, v]) => (
              <Stat key={k} label={v} value={int(d.states[k] ?? 0)} tone={k === 'blocked' && d.states[k] ? 'danger' : undefined} />
            ))}
          </div>

          <Panel
            title="Daily digital batch"
            actions={
              isLead && (
                <Button variant="primary" disabled={!d.batch_pending || batch.isPending} onClick={() => setConfirm(true)}>
                  Approve batch ({int(d.batch_pending)})
                </Button>
              )
            }
          >
            <p className="text-sm text-muted">
              {int(d.batch_pending)} of {int(d.batch_total)} digital reminders are waiting for sign-off. Digital reminders have no
              agent; the team lead's approval is the only sign-off.
              {!isLead && ' Only a team lead can approve the batch.'}
            </p>
            {batch.error && (
              <div className="mt-3">
                <ErrorState error={batch.error} />
              </div>
            )}
            {batch.isSuccess && <p className="mt-3 text-sm text-ok">Batch approved.</p>}
          </Panel>

          <Panel title={`Approvals waiting (${int(d.pending.rows.length)})`}>
            <DataTable
              data={d.pending}
              rowKey="recommendation_id"
              empty="Nothing is waiting for approval."
              caption="Approvals waiting"
              onRowClick={(r) => nav(`/cases/${encodeURIComponent(String(r.recommendation_id))}`)}
              columns={[
                { key: 'recommendation_id', label: 'Recommendation', render: (r) => <span className="font-mono text-xs">{String(r.recommendation_id)}</span> },
                { key: 'approval_reason', label: 'Needs approval for', render: (r) => <Badge tone="info">{label(APPROVAL_LABEL, r.approval_reason as string)}</Badge> },
                { key: 'summary', label: 'Summary', wrap: true, render: (r) => <span className="line-clamp-2 text-muted">{String(r.summary ?? '')}</span> },
                { key: 'risk_score', label: 'Risk', align: 'right', render: (r) => pct(r.risk_score, 0) },
              ]}
            />
          </Panel>

          <WeeklyReview summary={summary.data} error={summary.error} />
        </div>
      )}

      <Dialog open={confirm} onClose={() => setConfirm(false)} title="Approve the digital batch?">
        <p>
          This approves {int(d?.batch_pending ?? 0)} digital reminders for {isoDate(d?.as_of_date)}. Each one is recorded under your id
          ({me.agent_id}). To hold a case back, open it from the queue and reject it first.
        </p>
        <div className="mt-5 flex justify-end gap-3">
          <Button variant="ghost" onClick={() => setConfirm(false)}>
            Cancel
          </Button>
          <Button variant="primary" disabled={batch.isPending} onClick={() => batch.mutate()}>
            {batch.isPending ? 'Approving…' : `Approve ${int(d?.batch_pending ?? 0)}`}
          </Button>
        </div>
      </Dialog>
    </>
  )
}

function WeeklyReview({ summary, error }: { summary?: ReviewSummary; error: unknown }) {
  const reasons = useQuery({ queryKey: ['reasons'], queryFn: () => api.get<Reasons>('/api/review/reasons') })
  const text = (code: string) => reasons.data?.reasons.find((r) => r.code === code)?.label ?? code
  if (error) return <ErrorState error={error} />
  if (!summary) return null
  return (
    <Panel title={`Override review · ${isoDate(summary.from)} to ${isoDate(summary.to)}`}>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="Recommendations" value={int(summary.recommendations)} />
        <Stat label="Agent decisions" value={int(summary.agent_decisions)} />
        <Stat label="Override rate" value={pct(summary.override_rate)} />
      </div>
      <DataTable
        data={Object.entries(summary.overrides_by_reason).map(([code, n]) => ({
          reason: text(code),
          safeguard: reasons.data?.safeguard_reasons.includes(code) ?? false,
          overrides: n,
        }))}
        empty="No overrides in this period."
        caption="Overrides by reason"
        columns={[
          { key: 'reason', label: 'Reason' },
          { key: 'safeguard', label: 'Safeguard', render: (r) => (r.safeguard ? <Badge tone="warn">Duty of care</Badge> : '—') },
          { key: 'overrides', label: 'Overrides', align: 'right' },
        ]}
      />
    </Panel>
  )
}
