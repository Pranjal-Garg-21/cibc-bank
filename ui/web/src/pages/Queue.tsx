import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { QueueResponse } from '../api/types'
import { useMe } from '../auth'
import { Badge, DataTable, ErrorState, NotConnected, PageHeader, Spinner, Stat } from '../components/ui'
import { amount, int, isoDate, pct } from '../lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '../lib/labels'

export function Queue() {
  const me = useMe()
  const nav = useNavigate()
  const q = useQuery({
    queryKey: ['queue', 'mine'],
    queryFn: () => api.get<QueueResponse>('/api/queue/mine'),
    enabled: me.features.queue,
  })
  const rows = q.data?.queue.rows.map((r) => Object.fromEntries(q.data!.queue.columns.map((c, i) => [c, r[i]]))) ?? []
  const count = (state: string) => rows.filter((r) => r.state === state).length

  return (
    <>
      <PageHeader
        title="My queue"
        subtitle={`Today's recommendations for ${me.agent_id}, in working order: support calls first, then calls, then other contact.`}
      />
      {!me.features.queue && <NotConnected feature="The agent queue" owner="P3" />}
      {q.isLoading && <Spinner />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Recommendations" value={int(rows.length)} hint={`As of ${isoDate(q.data.as_of_date)}`} />
            <Stat label="Agent to decide" value={int(count('waiting_agent'))} tone={count('waiting_agent') ? 'warn' : undefined} />
            <Stat label="Waiting approval" value={int(count('waiting_approval'))} />
            <Stat label="Ready" value={int(count('ready'))} tone="ok" />
          </div>
          <DataTable
            data={rows}
            rowKey="recommendation_id"
            empty="No recommendations for you today."
            caption="My recommendations"
            onRowClick={(r) => nav(`/cases/${encodeURIComponent(String(r.recommendation_id))}`)}
            columns={[
              { key: 'action', label: 'Action', render: (r) => <span className="font-medium">{label(ACTION_LABEL, r.action as string)}</span> },
              { key: 'case_id', label: 'Case', render: (r) => <span className="font-mono text-xs">{String(r.case_id)}</span> },
              { key: 'summary', label: 'Why', wrap: true, render: (r) => <span className="line-clamp-2 text-muted">{String(r.summary ?? '')}</span> },
              { key: 'risk_score', label: 'Risk', align: 'right', render: (r) => pct(r.risk_score, 0) },
              { key: 'expected_value', label: 'Expected value (CAD)', align: 'right', render: (r) => amount(r.expected_value) },
              {
                key: 'approval_reason',
                label: 'Approval',
                render: (r) => (r.approval_reason ? <Badge tone="info">{label(APPROVAL_LABEL, r.approval_reason as string)}</Badge> : '—'),
              },
              {
                key: 'state',
                label: 'State',
                render: (r) => <Badge tone={STATE_TONE[r.state as string] ?? 'neutral'}>{label(STATE_LABEL, r.state as string)}</Badge>,
              },
            ]}
          />
        </>
      )}
    </>
  )
}
