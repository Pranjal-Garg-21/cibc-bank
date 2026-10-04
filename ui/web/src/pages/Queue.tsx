import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '@/api/client'
import type { QueueResponse } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, Figures, Loading, NotConnected, PageHeader, Section, StatusBadge } from '@/components/app'
import { amount, int, isoDate, pct } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '@/lib/labels'

export function Queue() {
  const me = useMe()
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['queue', 'mine'], queryFn: () => api.get<QueueResponse>('/api/queue/mine'), enabled: me.features.queue })
  const [params, setParams] = useSearchParams()
  const state = params.get('state') ?? ''
  const action = params.get('action') ?? ''
  const all = q.data?.queue.rows.map((r) => Object.fromEntries(q.data!.queue.columns.map((c, i) => [c, r[i]]))) ?? []
  const rows = all.filter((r) => (!state || r.state === state) && (!action || r.action === action))
  const setFilter = (k: 'state' | 'action', v: string) => {
    const next = new URLSearchParams(params)
    v && next.get(k) !== v ? next.set(k, v) : next.delete(k)
    setParams(next, { replace: true })
  }
  const count = (st: string) => all.filter((r) => r.state === st).length

  return (
    <>
      <PageHeader
        title="My work queue"
        description="Today's recommended contacts, in working order: support calls first, then calls, then other contact."
      />
      {!me.features.queue && <NotConnected feature="The work queue" owner="P3" />}
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div className="space-y-5">
          <Figures
            items={[
              { label: 'Recommendations', value: int(all.length), note: `Business date ${isoDate(q.data.as_of_date)}` },
              { label: 'Waiting for your decision', value: int(count('waiting_agent')), tone: count('waiting_agent') ? 'warn' : undefined },
              { label: 'Waiting for approval', value: int(count('waiting_approval')) },
              { label: 'Ready', value: int(count('ready')) },
            ]}
          />
          <Section title={`Cases assigned to you (${int(rows.length)}${rows.length !== all.length ? ` of ${int(all.length)}` : ''})`} flush>
            <div className="flex flex-wrap items-center gap-2 border-b border-border bg-white p-3" role="group" aria-label="Filter the queue">
              {Object.entries(STATE_LABEL).filter(([k]) => count(k) > 0).map(([k, v]) => (
                <button key={k} type="button" aria-pressed={state === k} onClick={() => setFilter('state', k)}
                  className={cn('rounded-sm border px-3 py-1 text-sm', state === k ? 'border-brand bg-[#f5e3e8] font-bold text-brand-dark' : 'border-border text-muted-foreground hover:border-[#8a8d90]')}>
                  {v} ({count(k)})
                </button>
              ))}
              {[...new Set(all.map((r) => String(r.action)))].map((a) => (
                <button key={a} type="button" aria-pressed={action === a} onClick={() => setFilter('action', a)}
                  className={cn('rounded-sm border px-3 py-1 text-sm', action === a ? 'border-brand bg-[#f5e3e8] font-bold text-brand-dark' : 'border-border text-muted-foreground hover:border-[#8a8d90]')}>
                  {label(ACTION_LABEL, a)}
                </button>
              ))}
              {(state || action) && <button type="button" className="ml-auto text-sm text-brand underline underline-offset-4" onClick={() => setParams({}, { replace: true })}>Clear filters</button>}
            </div>
            <DataTable
              sortable
              searchable={all.length > 6}
              data={rows}
              rowKey="recommendation_id"
              empty="You have no recommendations today."
              caption="My recommendations"
              onRowClick={(r) => nav(`/cases/${encodeURIComponent(String(r.recommendation_id))}`)}
              columns={[
                { key: 'action', label: 'Recommended action', render: (r) => label(ACTION_LABEL, r.action as string) },
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
