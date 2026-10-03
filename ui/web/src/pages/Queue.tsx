import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import type { QueueResponse } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, Figures, Loading, NotConnected, PageHeader, Section, StatusBadge } from '@/components/app'
import { amount, int, isoDate, pct } from '@/lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '@/lib/labels'

export function Queue() {
  const me = useMe()
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['queue', 'mine'], queryFn: () => api.get<QueueResponse>('/api/queue/mine'), enabled: me.features.queue })
  const rows = q.data?.queue.rows.map((r) => Object.fromEntries(q.data!.queue.columns.map((c, i) => [c, r[i]]))) ?? []
  const count = (state: string) => rows.filter((r) => r.state === state).length

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
              { label: 'Recommendations', value: int(rows.length), note: `Business date ${isoDate(q.data.as_of_date)}` },
              { label: 'Waiting for your decision', value: int(count('waiting_agent')), tone: count('waiting_agent') ? 'warn' : undefined },
              { label: 'Waiting for approval', value: int(count('waiting_approval')) },
              { label: 'Ready', value: int(count('ready')) },
            ]}
          />
          <Section title="Cases assigned to you" flush>
            <DataTable
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
