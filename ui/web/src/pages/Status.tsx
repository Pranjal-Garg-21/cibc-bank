import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Status } from '../api/types'
import { Badge, DataTable, ErrorState, PageHeader, Panel, Spinner, Stat } from '../components/ui'
import { dateTime, int, isoDate } from '../lib/format'

export function StatusPage() {
  const q = useQuery({ queryKey: ['status'], queryFn: () => api.get<Status>('/api/status') })
  const s = q.data
  return (
    <>
      <PageHeader title="Data status" subtitle="The last publish of the gold layer, its quality rules, and the reports." />
      {q.isLoading && <Spinner />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {s && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="As of" value={isoDate(s.as_of_date)} />
            <Stat
              label="Last publish"
              value={s.publish ? (s.publish.published ? 'Published' : 'Blocked') : '—'}
              tone={s.publish ? (s.publish.published ? 'ok' : 'danger') : undefined}
              hint={s.publish ? dateTime(s.publish.attempt_ts) : undefined}
            />
            <Stat label="Rules passed" value={`${s.rules.filter((r) => r.passed).length} / ${s.rules.length}`} />
            <Stat label="Customers (gold)" value={int(s.row_counts.c360_customer)} />
          </div>
          <Panel title="Quality rules">
            <DataTable
              data={s.rules as unknown as Record<string, unknown>[]}
              rowKey="rule_id"
              caption="Quality rules"
              columns={[
                { key: 'rule_id', label: 'Rule', render: (r) => <span className="font-mono text-xs">{String(r.rule_id)}</span> },
                { key: 'title', label: 'Checks', wrap: true },
                { key: 'blocking', label: 'Type', render: (r) => (r.blocking ? 'Blocks publish' : 'Warning') },
                { key: 'violations', label: 'Violations', align: 'right' },
                { key: 'total', label: 'Rows checked', align: 'right' },
                {
                  key: 'passed',
                  label: 'Result',
                  render: (r) => (r.passed ? <Badge tone="ok">Pass</Badge> : <Badge tone={r.blocking ? 'danger' : 'warn'}>{r.blocking ? 'Fail' : 'Warn'}</Badge>),
                },
              ]}
            />
          </Panel>
          <div className="grid gap-6 lg:grid-cols-2">
            <Panel title="Gold tables">
              <DataTable
                data={Object.entries(s.row_counts).map(([table, rows]) => ({ table, rows }))}
                caption="Gold tables"
                columns={[
                  { key: 'table', label: 'Table', render: (r) => <span className="font-mono text-xs">gold.{String(r.table)}</span> },
                  { key: 'rows', label: 'Rows', align: 'right' },
                ]}
              />
            </Panel>
            <Panel title="Reports">
              <ul className="divide-y divide-border">
                {s.reports.map((r) => (
                  <li key={r.name} className="flex items-center justify-between py-2 text-sm">
                    <Link to={`/status/reports/${r.name}`} className="text-accent-text hover:underline">
                      {r.title}
                    </Link>
                    <span className="text-xs text-faint">{dateTime(r.modified)}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          </div>
        </div>
      )}
    </>
  )
}

export function ReportPage() {
  const { name = '' } = useParams()
  const q = useQuery({
    queryKey: ['report', name],
    queryFn: () => api.get<{ name: string; markdown: string }>(`/api/status/reports/${encodeURIComponent(name)}`),
  })
  return (
    <>
      <Link to="/status" className="mb-3 inline-block text-sm text-accent-text hover:underline">
        ← Back to data status
      </Link>
      {q.isLoading && <Spinner />}
      {q.error && <ErrorState error={q.error} />}
      {q.data && (
        <Panel title={<span className="font-mono">{q.data.name}</span>}>
          {/* Shown as text, not rendered HTML. */}
          <pre className="overflow-x-auto font-mono text-[13px] leading-relaxed whitespace-pre-wrap text-muted">{q.data.markdown}</pre>
        </Panel>
      )}
    </>
  )
}
