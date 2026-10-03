import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import type { Digest, Reasons, ReviewSummary } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, Figures, Loading, NotConnected, Notice, PageHeader, Section } from '@/components/app'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { int, isoDate, pct } from '@/lib/format'
import { APPROVAL_LABEL, STATE_LABEL, label } from '@/lib/labels'

export function Approvals() {
  const me = useMe()
  const nav = useNavigate()
  const qc = useQueryClient()
  const digest = useQuery({ queryKey: ['digest'], queryFn: () => api.get<Digest>('/api/queue/digest'), enabled: me.features.queue })
  const summary = useQuery({ queryKey: ['summary'], queryFn: () => api.get<ReviewSummary>('/api/review/summary?days=7'), enabled: me.features.queue })
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
      <PageHeader title="Team & approvals" description="Where today's recommendations stand across the team, and the approvals waiting on you." />
      {!me.features.queue && <NotConnected feature="Approvals" owner="P3" />}
      {digest.isLoading && <Loading />}
      {digest.error && <ErrorState error={digest.error} onRetry={() => digest.refetch()} />}
      {d && (
        <div className="space-y-5">
          <Figures items={Object.entries(STATE_LABEL).map(([k, v]) => ({ label: v, value: int(d.states[k] ?? 0), tone: k === 'blocked' && d.states[k] ? 'danger' : undefined }))} />

          <Section
            title="Daily digital batch"
            description={`Business date ${isoDate(d.as_of_date)}`}
            actions={
              isLead && (
                <Button disabled={!d.batch_pending || batch.isPending} onClick={() => setConfirm(true)}>
                  Approve batch ({int(d.batch_pending)})
                </Button>
              )
            }
          >
            <p className="text-sm">
              {int(d.batch_pending)} of {int(d.batch_total)} digital reminders are waiting for sign-off. Digital reminders have no agent, so the
              team lead's approval is the only sign-off.{!isLead && ' Only a team lead can approve the batch.'}
            </p>
            {batch.error && (
              <div className="mt-3">
                <ErrorState error={batch.error} />
              </div>
            )}
            {batch.isSuccess && (
              <div className="mt-3">
                <Notice tone="ok">Batch approved and recorded under {me.agent_id}.</Notice>
              </div>
            )}
          </Section>

          <Section title={`Waiting for approval (${int(d.pending.rows.length)})`} description="Open a case to approve or reject it" flush>
            <DataTable
              data={d.pending}
              rowKey="recommendation_id"
              empty="Nothing is waiting for approval."
              caption="Waiting for approval"
              onRowClick={(r) => nav(`/cases/${encodeURIComponent(String(r.recommendation_id))}`)}
              columns={[
                { key: 'recommendation_id', label: 'Recommendation' },
                { key: 'approval_reason', label: 'Approval needed for', render: (r) => label(APPROVAL_LABEL, r.approval_reason as string) },
                { key: 'summary', label: 'Summary', wrap: true, render: (r) => <span className="line-clamp-2">{String(r.summary ?? '')}</span> },
                { key: 'risk_score', label: 'Risk', align: 'right', render: (r) => pct(r.risk_score, 0) },
              ]}
            />
          </Section>

          <WeeklyReview summary={summary.data} error={summary.error} />
        </div>
      )}

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent className="rounded-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-normal">Approve the digital batch?</AlertDialogTitle>
            <AlertDialogDescription>
              This approves {int(d?.batch_pending ?? 0)} digital reminders for {isoDate(d?.as_of_date)}. Each approval is recorded under your staff ID
              ({me.agent_id}). To hold a case back, open it and reject it first.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction disabled={batch.isPending} onClick={(e) => (e.preventDefault(), batch.mutate())}>
              {batch.isPending ? 'Approving…' : `Approve ${int(d?.batch_pending ?? 0)}`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function WeeklyReview({ summary, error }: { summary?: ReviewSummary; error: unknown }) {
  const reasons = useQuery({ queryKey: ['reasons'], queryFn: () => api.get<Reasons>('/api/review/reasons') })
  const text = (code: string) => reasons.data?.reasons.find((r) => r.code === code)?.label ?? code
  if (error) return <ErrorState error={error} />
  if (!summary) return null
  return (
    <Section title="Override review" description={`${isoDate(summary.from)} to ${isoDate(summary.to)}`}>
      <div className="mb-4">
        <Figures
          items={[
            { label: 'Recommendations', value: int(summary.recommendations) },
            { label: 'Agent decisions', value: int(summary.agent_decisions) },
            { label: 'Override rate', value: pct(summary.override_rate) },
          ]}
        />
      </div>
      <DataTable
        data={Object.entries(summary.overrides_by_reason).map(([code, n]) => ({
          reason: text(code),
          duty_of_care: reasons.data?.safeguard_reasons.includes(code) ? 'Yes' : '',
          overrides: n,
        }))}
        empty="No overrides in this period."
        caption="Overrides by reason"
        columns={[{ key: 'reason', label: 'Reason' }, { key: 'duty_of_care', label: 'Duty of care' }, { key: 'overrides', label: 'Overrides', align: 'right' }]}
      />
    </Section>
  )
}
