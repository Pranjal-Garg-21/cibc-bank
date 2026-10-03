import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Clock, Inbox, UserCheck } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import type { QueueResponse } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, Loading, NotConnected, PageHeader, Section, Stat, StatusBadge } from '@/components/app'
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
        eyebrow="Collections operations"
        title="My work queue"
        description={`Today's recommended contacts for ${me.agent_id}, in working order: support calls first, then calls, then other contact.`}
      />
      {!me.features.queue && <NotConnected feature="The work queue" owner="P3" />}
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Recommendations" value={int(rows.length)} hint={`Business date ${isoDate(q.data.as_of_date)}`} icon={<Inbox className="size-4" />} />
            <Stat label="Awaiting your decision" value={int(count('waiting_agent'))} tone={count('waiting_agent') ? 'warn' : undefined} icon={<UserCheck className="size-4" />} />
            <Stat label="Awaiting approval" value={int(count('waiting_approval'))} icon={<Clock className="size-4" />} />
            <Stat label="Ready to action" value={int(count('ready'))} tone="ok" icon={<CheckCircle2 className="size-4" />} />
          </div>
          <Section title="Cases assigned to you" description="Open a row to see the decision card and record your decision.">
            <DataTable
              data={rows}
              rowKey="recommendation_id"
              empty="No recommendations for you today."
              caption="My recommendations"
              onRowClick={(r) => nav(`/cases/${encodeURIComponent(String(r.recommendation_id))}`)}
              columns={[
                { key: 'action', label: 'Recommended action', render: (r) => <span className="font-medium">{label(ACTION_LABEL, r.action as string)}</span> },
                { key: 'case_id', label: 'Case', render: (r) => <span className="font-mono text-xs">{String(r.case_id)}</span> },
                { key: 'summary', label: 'Why', wrap: true, render: (r) => <span className="line-clamp-2 text-muted-foreground">{String(r.summary ?? '')}</span> },
                { key: 'risk_score', label: 'Risk', align: 'right', render: (r) => pct(r.risk_score, 0) },
                { key: 'expected_value', label: 'Expected value (CAD)', align: 'right', render: (r) => amount(r.expected_value) },
                {
                  key: 'approval_reason',
                  label: 'Approval',
                  render: (r) => (r.approval_reason ? <StatusBadge tone="info">{label(APPROVAL_LABEL, r.approval_reason as string)}</StatusBadge> : '—'),
                },
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
