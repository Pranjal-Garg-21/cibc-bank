import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, live } from '@/api/client'
import type { QueueResponse } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, Figures, Loading, NotConnected, PageHeader, Section, StatusBadge } from '@/components/app'
import { Input } from '@/components/ui/input'
import { amount, int, isoDate, pct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '@/lib/labels'

// Nothing left for the agent to do: these leave the day's list, and their filter brings them back (D36, D40).
const ARCHIVED = new Set(['done', 'overridden'])

const chip = (on: boolean) =>
  cn('rounded-sm border px-3 py-1 text-sm', on ? 'border-brand bg-[#f5e3e8] font-bold text-brand-dark' : 'border-border text-muted-foreground hover:border-[#8a8d90]')

/** Every case assigned to the signed-in agent today. The figures always count the whole queue (never the filtered
 * rows); the search covers every case, whatever the filters, including done and overridden ones. */
export function Queue() {
  const me = useMe()
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['queue', 'mine'], queryFn: () => api.poll<QueueResponse>('/api/queue/mine'), enabled: me.features.queue, ...live })
  const [params, setParams] = useSearchParams()
  const [term, setTerm] = useState('')
  const state = params.get('state') ?? ''
  const action = params.get('action') ?? ''
  const all = q.data?.queue.rows.map((r) => Object.fromEntries(q.data!.queue.columns.map((c, i) => [c, r[i]]))) ?? []
  const t = term.trim().toLowerCase()
  const matches = (r: Record<string, unknown>) =>
    [r.customer, r.case_id, r.recommendation_id, r.summary, label(ACTION_LABEL, r.action as string), label(STATE_LABEL, r.state as string),
      r.approval_reason ? label(APPROVAL_LABEL, r.approval_reason as string) : '']
      .some((v) => String(v ?? '').toLowerCase().includes(t))
  // a search looks through every case; without one, done and overridden cases stay out unless their filter is on
  const rows = all.filter((r) =>
    (t ? matches(r) : true) &&
    (state ? r.state === state : t || !ARCHIVED.has(String(r.state))) &&
    (!action || r.action === action))
  const setFilter = (k: 'state' | 'action', v: string) => {
    const next = new URLSearchParams(params)
    v && next.get(k) !== v ? next.set(k, v) : next.delete(k)
    setParams(next, { replace: true })
  }
  const count = (st: string) => all.filter((r) => r.state === st).length
  const active = all.filter((r) => !ARCHIVED.has(String(r.state))).length

  return (
    <>
      <PageHeader
        title="My work queue"
        description="Today's recommended contacts, in working order: support calls first, then calls, then other contact. Calls you mark complete, waits you accept and cases you override move out of the list; their filters bring them back."
      />
      {!me.features.queue && <NotConnected feature="The work queue" owner="P3" />}
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div className="space-y-5">
          <Figures
            items={[
              { label: 'Waiting for your decision', value: int(count('waiting_agent')), note: 'Calls to accept or override', tone: count('waiting_agent') ? 'warn' : undefined },
              { label: 'Cleared to carry out', value: int(count('ready')), note: 'Every sign-off is in' },
              { label: 'Done today', value: int(count('done')), note: `Business date ${isoDate(q.data.as_of_date)}` },
            ]}
          />
          <Section title={`Cases assigned to you (${int(rows.length)}${t || state || action ? ` of ${int(all.length)}` : ` open, ${int(all.length - active)} done or overridden`})`} flush>
            <div className="flex flex-wrap items-center gap-2 border-b border-border bg-white p-3" role="group" aria-label="Filter the queue">
              {Object.entries(STATE_LABEL).filter(([k]) => count(k) > 0).map(([k, v]) => (
                <button key={k} type="button" aria-pressed={state === k} onClick={() => setFilter('state', k)} className={chip(state === k)}>
                  {v} ({count(k)})
                </button>
              ))}
              {[...new Set(all.map((r) => String(r.action)))].map((a) => (
                <button key={a} type="button" aria-pressed={action === a} onClick={() => setFilter('action', a)} className={chip(action === a)}>
                  {label(ACTION_LABEL, a)}
                </button>
              ))}
              {(state || action) && <button type="button" className="ml-auto text-sm text-brand underline underline-offset-4" onClick={() => setParams({}, { replace: true })}>Clear filters</button>}
            </div>
            <div className="relative border-b border-border bg-white p-3">
              <Search className="pointer-events-none absolute top-1/2 left-5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Search all your cases: customer, case, treatment, status"
                aria-label="Search all your cases" className="h-9 max-w-md rounded-sm pl-8" />
              {t && <span className="ml-3 text-xs text-muted-foreground" role="status">{int(rows.length)} of {int(all.length)} cases match, including done and overridden</span>}
            </div>
            <DataTable
              sortable
              data={rows}
              rowKey="recommendation_id"
              empty={t ? 'No case matches your search.' : 'You have no recommendations today.'}
              caption="My recommendations"
              onRowClick={(r) => nav(`/cases/${encodeURIComponent(String(r.recommendation_id))}`)}
              columns={[
                { key: 'action', label: 'Recommended action', render: (r) => label(ACTION_LABEL, r.action as string) },
                { key: 'customer', label: 'Customer', render: (r) => (
                  <span>{String(r.customer ?? '-')}{r.todays_contact === false && <span className="block text-xs text-muted-foreground">Contacted on another case today</span>}</span>
                ) },
                { key: 'case_id', label: 'Case' },
                { key: 'summary', label: 'Reason', wrap: true, render: (r) => <span className="line-clamp-2">{String(r.summary ?? '')}</span> },
                { key: 'risk_score', label: 'Risk', align: 'right', render: (r) => pct(r.risk_score, 0) },
                { key: 'expected_value', label: 'Expected value (CAD)', align: 'right', render: (r) => amount(r.expected_value) },
                { key: 'approval_reason', label: 'Approval', render: (r) => (r.approval_reason ? label(APPROVAL_LABEL, r.approval_reason as string) : '-') },
                {
                  key: 'state',
                  label: 'Status',
                  render: (r) => <StatusBadge tone={STATE_TONE[r.state as string] ?? 'neutral'}>{label(STATE_LABEL, r.state as string)}</StatusBadge>,
                },
              ]}
            />
          </Section>
        </div>
      )}
    </>
  )
}
