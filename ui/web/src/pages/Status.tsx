import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, CalendarDays, FileText, ShieldCheck, Upload, Users } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/client'
import type { Status } from '@/api/types'
import { DataTable, ErrorState, Loading, PageHeader, Section, Stat, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { dateTime, int, isoDate } from '@/lib/format'

export function StatusPage() {
  const q = useQuery({ queryKey: ['status'], queryFn: () => api.get<Status>('/api/status') })
  const s = q.data
  return (
    <>
      <PageHeader
        eyebrow="Data governance"
        title="Data quality & status"
        description="The last publish of the customer 360 (gold) layer, the data-contract rules that gate it, and the quality reports."
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {s && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Business date" value={isoDate(s.as_of_date)} icon={<CalendarDays className="size-4" />} />
            <Stat
              label="Last publish"
              value={s.publish ? (s.publish.published ? 'Published' : 'Blocked') : '—'}
              tone={s.publish ? (s.publish.published ? 'ok' : 'danger') : undefined}
              hint={s.publish ? dateTime(s.publish.attempt_ts) : undefined}
              icon={<Upload className="size-4" />}
            />
            <Stat
              label="Contract rules passed"
              value={`${s.rules.filter((r) => r.passed).length} / ${s.rules.length}`}
              hint={s.rules.some((r) => !r.passed && r.blocking) ? 'A blocking rule failed' : 'No blocking failures'}
              icon={<ShieldCheck className="size-4" />}
            />
            <Stat label="Customers (golden records)" value={int(s.row_counts.c360_customer)} icon={<Users className="size-4" />} />
          </div>
          <Section title="Data-contract rules" description="Blocking rules stop the publish; warnings are reported">
            <DataTable
              data={s.rules as unknown as Record<string, unknown>[]}
              rowKey="rule_id"
              caption="Data-contract rules"
              columns={[
                { key: 'rule_id', label: 'Rule', render: (r) => <span className="font-mono text-xs">{String(r.rule_id)}</span> },
                { key: 'title', label: 'What it checks', wrap: true },
                { key: 'blocking', label: 'Type', render: (r) => (r.blocking ? 'Blocks publish' : 'Warning') },
                { key: 'violations', label: 'Violations', align: 'right' },
                { key: 'total', label: 'Rows checked', align: 'right' },
                {
                  key: 'passed',
                  label: 'Result',
                  render: (r) =>
                    r.passed ? <StatusBadge tone="ok">Pass</StatusBadge> : <StatusBadge tone={r.blocking ? 'danger' : 'warn'}>{r.blocking ? 'Fail' : 'Warn'}</StatusBadge>,
                },
              ]}
            />
          </Section>
          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="Customer 360 tables">
              <DataTable
                data={Object.entries(s.row_counts).map(([table, rows]) => ({ table, rows }))}
                caption="Gold tables"
                columns={[
                  { key: 'table', label: 'Table', render: (r) => <span className="font-mono text-xs">gold.{String(r.table)}</span> },
                  { key: 'rows', label: 'Rows', align: 'right' },
                ]}
              />
            </Section>
            <Section title="Reports">
              <ul className="divide-y divide-border">
                {s.reports.map((r) => (
                  <li key={r.name} className="flex items-center justify-between gap-3 py-2.5 text-sm first:pt-0 last:pb-0">
                    <Link to={`/status/reports/${r.name}`} className="flex items-center gap-2 text-info hover:underline">
                      <FileText className="size-4" />
                      {r.title}
                    </Link>
                    <span className="text-xs text-faint">{dateTime(r.modified)}</span>
                  </li>
                ))}
              </ul>
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
      <Button asChild variant="ghost" size="sm" className="mb-3 -ml-2 text-muted-foreground">
        <Link to="/status">
          <ArrowLeft /> Back to data status
        </Link>
      </Button>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} />}
      {q.data && (
        <Section title={<span className="font-mono">{q.data.name}</span>}>
          {/* Shown as text, not rendered HTML. */}
          <pre className="overflow-x-auto font-mono text-[13px] leading-relaxed whitespace-pre-wrap text-muted-foreground">{q.data.markdown}</pre>
        </Section>
      )}
    </>
  )
}
