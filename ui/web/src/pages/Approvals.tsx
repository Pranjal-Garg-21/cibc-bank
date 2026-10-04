import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, ApiError, DECISION_QUERIES, live } from '@/api/client'
import type { Digest } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, Loading, NotConnected, Notice, PageHeader, Section } from '@/components/app'
import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { dateTime, int, isoDate, pct } from '@/lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, BATCH_EXPLAINED, label } from '@/lib/labels'

const rowsOf = (t?: { columns: string[]; rows: unknown[][] }) =>
  (t?.rows ?? []).map((r) => Object.fromEntries((t?.columns ?? []).map((c, i) => [c, r[i]])))

const chip = (on: boolean) =>
  cn('rounded-sm border px-3 py-1 text-sm', on ? 'border-brand bg-[#f5e3e8] font-bold text-brand-dark' : 'border-border text-muted-foreground hover:border-[#8a8d90]')

/** Approvals waiting on this role, the daily batch of automated reminders (team lead), and the archive of cases their
 * agent overrode (they need no sign-off, so they leave the waiting list; the Overridden view brings them back, with
 * who overrode each and why). Refreshed every 20 s and on focus, so decisions made elsewhere show up. */
export function Approvals() {
  const me = useMe()
  const nav = useNavigate()
  const digest = useQuery({ queryKey: ['digest'], queryFn: () => api.poll<Digest>('/api/queue/digest'), enabled: me.features.queue, ...live })
  const [params, setParams] = useSearchParams()
  const reason = params.get('reason') ?? ''
  const view = params.get('view') === 'overridden' ? 'overridden' : 'waiting'
  const isLead = me.can.approve_batch
  const d = digest.data
  const allRows = rowsOf(d?.pending)
  const overridden = rowsOf(d?.overridden)
  const pendingRows = reason ? allRows.filter((r) => r.approval_reason === reason) : allRows
  const open = (r: Record<string, unknown>) => nav(`/cases/${encodeURIComponent(String(r.recommendation_id))}`)

  return (
    <>
      <PageHeader
        title={isLead ? 'Team & approvals' : 'Hardship & vulnerability approvals'}
        description={isLead ? 'The approvals waiting on you, and the daily batch of automated reminders.' : 'Customers who may be struggling or vulnerable. Check the card, then approve or reject. You cannot approve high balances or automated reminders.'}
      />
      {!me.features.queue && <NotConnected feature="Approvals" owner="P3" />}
      {digest.isLoading && <Loading />}
      {digest.error && <ErrorState error={digest.error} onRetry={() => digest.refetch()} />}
      {d && (
        <div className="space-y-5">
          {isLead && <BatchSection d={d} />}

          <Section
            title={view === 'waiting' ? `Waiting for approval (${int(pendingRows.length)})` : `Overridden by agents (${int(overridden.length)})`}
            description={view === 'waiting' ? 'Open a case to approve or reject it' : 'Archived: an override needs no sign-off. Open a case to see the card and its history.'}
            flush
          >
            <div className="flex flex-wrap items-center gap-2 border-b border-border bg-white p-3" role="group" aria-label="Filter the list">
              {view === 'waiting' && [{ k: '', n: allRows.length }, ...(d.approval_kinds ?? []).filter((k) => k !== 'digital_batch').map((k) => ({ k, n: allRows.filter((r) => r.approval_reason === k).length }))].map(({ k, n }) => (
                <button key={k || 'all'} type="button" aria-pressed={reason === k} onClick={() => setParams(k ? { reason: k } : {}, { replace: true })} className={chip(reason === k)}>
                  {k ? label(APPROVAL_LABEL, k) : 'All waiting'} ({n})
                </button>
              ))}
              <button type="button" aria-pressed={view === 'overridden'} className={cn(chip(view === 'overridden'), 'ml-auto')}
                onClick={() => setParams(view === 'overridden' ? {} : { view: 'overridden' }, { replace: true })}>
                {view === 'overridden' ? 'Back to waiting for approval' : `Overridden (${int(overridden.length)})`}
              </button>
            </div>
            {view === 'waiting' ? (
              <DataTable
                data={pendingRows}
                rowKey="recommendation_id"
                empty="Nothing is waiting for approval."
                caption="Waiting for approval"
                sortable
                searchable={allRows.length > 6}
                onRowClick={open}
                columns={[
                  { key: 'recommendation_id', label: 'Recommendation' },
                  { key: 'approval_reason', label: 'Approval needed for', render: (r) => label(APPROVAL_LABEL, r.approval_reason as string) },
                  { key: 'summary', label: 'Summary', wrap: true, render: (r) => <span className="line-clamp-2">{String(r.summary ?? '')}</span> },
                  { key: 'risk_score', label: 'Risk', align: 'right', render: (r) => pct(r.risk_score, 0) },
                ]}
              />
            ) : (
              <DataTable
                data={overridden}
                rowKey="recommendation_id"
                empty="No case has been overridden today."
                caption="Overridden by agents"
                sortable
                searchable={overridden.length > 6}
                onRowClick={open}
                columns={[
                  { key: 'case_id', label: 'Case' },
                  { key: 'action', label: 'Recommended', render: (r) => label(ACTION_LABEL, r.action as string) },
                  { key: 'overridden_by', label: 'Overridden by', render: (r) => r.overridden_by_name ? `${String(r.overridden_by_name)} (${String(r.overridden_by)})` : String(r.overridden_by ?? '-') },
                  { key: 'reason', label: 'Reason', wrap: true },
                  { key: 'approval_reason', label: 'Approval it needed', render: (r) => (r.approval_reason ? label(APPROVAL_LABEL, r.approval_reason as string) : '-') },
                  { key: 'overridden_at', label: 'When', render: (r) => dateTime(r.overridden_at) },
                ]}
              />
            )}
          </Section>
        </div>
      )}
    </>
  )
}

/** The daily batch: every automated reminder of the day, signed off at once. Confirmed with the password every time
 * (D38): the password goes with the approval itself, so an earlier re-authentication never stands in for it. */
function BatchSection({ d }: { d: Digest }) {
  const me = useMe()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const close = () => { setConfirm(false); setPassword(''); setCode('') }
  const batch = useMutation({
    mutationFn: () => api.post<{ result: { approved: number; rejected: number } }>('/api/review-batch', { password, verification_code: code, rejected: {} }),
    onSuccess: () => {
      close()
      DECISION_QUERIES.forEach((queryKey) => void qc.invalidateQueries({ queryKey }))
    },
    onSettled: () => { setPassword(''); setCode('') },
  })
  const wrong = batch.error instanceof ApiError && batch.error.code === 'verification_failed'
  return (
    <Section
      title="Automated reminders (daily batch)"
      description={`Business date ${isoDate(d.as_of_date)}`}
      actions={
        <Button disabled={!d.batch_pending || batch.isPending} onClick={() => { batch.reset(); setConfirm(true) }}>
          Approve batch ({int(d.batch_pending)})
        </Button>
      }
    >
      <p className="text-sm">{BATCH_EXPLAINED}</p>
      <p className="mt-2 text-sm text-muted-foreground">
        {d.batch_pending ? `${int(d.batch_pending)} of ${int(d.batch_total)} reminders are waiting for your sign-off. To pull a case out, open it and reject it first.` : `All ${int(d.batch_total)} reminders are signed off.`}
      </p>
      {batch.error && !confirm && <div className="mt-3"><ErrorState error={batch.error} /></div>}
      {batch.isSuccess && (
        <div className="mt-3">
          <Notice tone="ok">Batch approved ({int(batch.data.result.approved)} reminders) and recorded under {me.agent_id}.</Notice>
        </div>
      )}
      <AlertDialog open={confirm} onOpenChange={(o) => !o && !batch.isPending && close()}>
        <AlertDialogContent className="rounded-sm">
          <form onSubmit={(e) => { e.preventDefault(); if (password && !batch.isPending) batch.mutate() }}>
            <AlertDialogHeader>
              <AlertDialogTitle className="font-normal">Approve the daily batch?</AlertDialogTitle>
              <AlertDialogDescription>
                This approves {int(d.batch_pending)} automated reminders for {isoDate(d.as_of_date)}, recorded under your staff ID ({me.agent_id}).
                Enter your password to confirm. It is asked for every batch.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="my-4 space-y-3">
              <div>
                <Label htmlFor="batch-password" className="mb-2">Your password</Label>
                <Input id="batch-password" type="password" autoComplete="current-password" autoFocus required maxLength={128}
                  className="h-11" value={password} onChange={(e) => setPassword(e.target.value)} disabled={batch.isPending} />
              </div>
              {me.mfa && (
                <div>
                  <Label htmlFor="batch-code" className="mb-2">Verification code</Label>
                  <Input id="batch-code" autoComplete="one-time-code" spellCheck={false} autoCapitalize="none" required maxLength={32}
                    className="h-11 font-mono" value={code} onChange={(e) => setCode(e.target.value)} disabled={batch.isPending} />
                </div>
              )}
              {batch.error && <p role="alert" className="text-sm text-danger">{wrong ? 'The password or verification code is incorrect.' : batch.error instanceof ApiError ? batch.error.message : 'Something went wrong.'}</p>}
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel type="button" disabled={batch.isPending}>Cancel</AlertDialogCancel>
              <Button type="submit" disabled={!password || batch.isPending}>
                {batch.isPending ? 'Approving…' : `Approve ${int(d.batch_pending)}`}
              </Button>
            </AlertDialogFooter>
          </form>
        </AlertDialogContent>
      </AlertDialog>
    </Section>
  )
}
