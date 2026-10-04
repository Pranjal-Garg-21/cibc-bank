import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/client'
import type { Status } from '@/api/types'
import { DataTable, ErrorState, Figures, Loading, PageHeader, Section, StatusBadge } from '@/components/app'
import { dateTime, int, isoDate } from '@/lib/format'

export function StatusPage() {
  const q = useQuery({ queryKey: ['status'], queryFn: () => api.get<Status>('/api/status') })
  const s = q.data
  return (
    <>
      <PageHeader
        title="Data quality"
        description="The latest publish of the customer 360 data, the data-contract rules that gate it, and the quality reports."
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {s && (
        <div className="space-y-5">
          <Figures
            items={[
              { label: 'Business date', value: isoDate(s.as_of_date) },
              {
                label: 'Last publish',
                value: s.publish ? (s.publish.published ? 'Published' : 'Blocked') : '-',
                tone: s.publish ? (s.publish.published ? 'ok' : 'danger') : undefined,
                note: s.publish ? dateTime(s.publish.attempt_ts) : undefined,
              },
              {
                label: 'Contract rules passed',
                value: `${s.rules.filter((r) => r.passed).length} of ${s.rules.length}`,
                note: s.rules.some((r) => !r.passed && r.blocking) ? 'A blocking rule failed' : 'No blocking failures',
              },
              { label: 'Customers (golden records)', value: int(s.row_counts.c360_customer) },
              {
                label: 'Ask language model',
                value: s.ask_model.enabled ? (s.ask_model.key_configured ? 'On' : 'On, no key') : 'Off',
                tone: s.ask_model.enabled && s.ask_model.key_configured ? 'ok' : 'warn',
                note: s.ask_model.enabled ? undefined : 'Metrics only; start with --ask-llm',
              },
            ]}
          />
          <Section title="Data-contract rules" description="Blocking rules stop the publish; warnings are reported" flush>
            <DataTable
              data={s.rules as unknown as Record<string, unknown>[]}
              rowKey="rule_id"
              caption="Data-contract rules"
              columns={[
                { key: 'rule_id', label: 'Rule' },
                { key: 'title', label: 'What it checks', wrap: true },
                { key: 'blocking', label: 'Type', render: (r) => (r.blocking ? 'Blocks publish' : 'Warning') },
                { key: 'violations', label: 'Exceptions', align: 'right' },
                { key: 'total', label: 'Rows checked', align: 'right' },
                {
                  key: 'passed',
                  label: 'Result',
                  render: (r) => (r.passed ? <StatusBadge tone="ok">Pass</StatusBadge> : <StatusBadge tone={r.blocking ? 'danger' : 'warn'}>{r.blocking ? 'Fail' : 'Warning'}</StatusBadge>),
                },
              ]}
            />
          </Section>
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <Section title="Customer 360 tables" flush>
              <DataTable
                data={Object.entries(s.row_counts).map(([table, rows]) => ({ table, rows }))}
                caption="Gold tables"
                columns={[
                  { key: 'table', label: 'Table', render: (r) => <span className="font-mono text-[13px]">gold.{String(r.table)}</span> },
                  { key: 'rows', label: 'Rows', align: 'right' },
                ]}
              />
            </Section>
            <Section title="Reports" flush>
              <table className="w-full text-sm">
                <tbody>
                  {s.reports.map((r) => (
                    <tr key={r.name} className="border-b border-[#e6e6e6] last:border-b-0">
                      <td className="px-3 py-2">
                        <Link to={`/status/reports/${r.name}`}>{r.title}</Link>
                      </td>
                      <td className="px-3 py-2 text-right text-xs text-muted-foreground">Updated {dateTime(r.modified)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>
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
      <p className="mb-3 text-sm">
        <Link to="/status">‹ Back to data quality</Link>
      </p>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} />}
      {q.data && (
        <Section title={q.data.name}>
          {/* Shown as text, not rendered HTML. */}
          <pre className="overflow-x-auto font-mono text-[13px] leading-relaxed whitespace-pre-wrap">{q.data.markdown}</pre>
        </Section>
      )}
    </>
  )
}
