import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/client'
import type { WarehouseCatalog, WarehouseTable } from '@/api/types'
import { DataTable, ErrorState, Loading, Notice, PageHeader, Section, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { int } from '@/lib/format'
import { cn } from '@/lib/utils'

export function Tables() {
  const { schema = 'curated', name = 'agents' } = useParams()
  const [filter, setFilter] = useState('')
  const catalog = useQuery({ queryKey: ['warehouse-tables'], queryFn: () => api.get<WarehouseCatalog>('/api/tables') })
  const entries = (catalog.data?.tables ?? []).filter((t) => `${t.schema}.${t.name}`.includes(filter.trim().toLowerCase()))
  return <>
    <PageHeader title="Tables" description="Browse the warehouse's actual tables, columns and rows. Empty tables show their column structure." />
    <div className="grid min-w-0 gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
      <Section title={`Warehouse tables${catalog.data ? ` (${catalog.data.tables.length})` : ''}`}>
        <Label htmlFor="table-filter" className="mb-2">Find a table</Label>
        <Input id="table-filter" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search table names" className="mb-4 h-11" />
        {catalog.isLoading && <Loading rows={3} />}
        {catalog.error && <ErrorState error={catalog.error} onRetry={() => catalog.refetch()} />}
        <nav aria-label="Warehouse tables" className="max-h-[520px] overflow-y-auto">
          {entries.map((t) => <Link key={`${t.schema}.${t.name}`} to={`/tables/${t.schema}/${t.name}`}
            aria-current={schema === t.schema && name === t.name ? 'page' : undefined}
            className={cn('flex min-h-12 items-center justify-between gap-2 border-b border-border px-2 py-3 text-sm text-foreground no-underline hover:bg-panel', schema === t.schema && name === t.name && 'border-l-2 border-l-brand bg-panel font-bold text-brand-dark')}>
            <span className="min-w-0 break-all"><span className="block text-xs font-normal text-muted-foreground">{t.schema}</span>{t.name}</span>
            <span className="shrink-0 text-xs tabular-nums">{int(t.rows)}</span>
          </Link>)}
          {catalog.data && !entries.length && <p className="py-3 text-sm text-muted-foreground">No matching tables.</p>}
        </nav>
      </Section>
      <TableRows key={`${schema}.${name}`} schema={schema} name={name} />
    </div>
  </>
}

function TableRows({ schema, name }: { schema: string; name: string }) {
  const [offset, setOffset] = useState(0)
  const [showColumns, setShowColumns] = useState(false)
  const q = useQuery({ queryKey: ['warehouse-table', schema, name, offset],
    queryFn: () => api.get<WarehouseTable>(`/api/tables/${encodeURIComponent(schema)}/${encodeURIComponent(name)}?offset=${offset}&limit=50`) })
  const d = q.data
  return <div className="min-w-0 space-y-4">
    {q.isLoading && <Loading />}
    {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
    {d && <>
      <Section title={`${schema}.${name}`} description={`${int(d.total)} rows · ${d.columns.length} columns · read-only`}
        actions={<StatusBadge tone={d.total ? 'neutral' : 'warn'}>{d.total ? 'Rows available' : 'Empty table'}</StatusBadge>} flush>
        <div className="border-b border-border px-5 py-3 text-xs text-muted-foreground">Sensitive and unclassified values appear as ***. Long text is limited to 2,000 characters.</div>
        <DataTable data={d.data} columns={d.columns.map((c) => ({ key: c.name, label: c.name }))} showEmptyHeaders
          caption={`${schema}.${name} rows`} empty={d.total ? 'No rows on this page. Select Previous to return to earlier rows.' : 'This table exists but has no rows yet. Its columns are shown above.'} />
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-5 py-4">
          <span className="text-sm tabular-nums">{d.data.rows.length ? `Rows ${int(offset + 1)}-${int(Math.min(offset + d.data.rows.length, d.total))} of ${int(d.total)}` : d.total ? 'No rows on this page' : '0 rows'}</span>
          <span className="flex gap-2"><Button variant="outline" size="sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>
            <Button variant="outline" size="sm" disabled={offset + 50 >= d.total || offset + 50 > 1_000_000} onClick={() => setOffset(offset + 50)}>Next</Button></span>
        </div>
      </Section>
      {!d.total && <Notice tone="info">The database contains the table definition. Rows will appear here when its pipeline populates it.</Notice>}
      <Button variant="outline" aria-expanded={showColumns} aria-controls="table-column-details" onClick={() => setShowColumns((v) => !v)}>{showColumns ? 'Hide' : 'Show'} columns and types</Button>
      {showColumns && <div id="table-column-details"><Section title="Columns and types" flush><DataTable data={d.columns as unknown as Record<string, unknown>[]} caption="Column definitions"
        columns={[{ key: 'name', label: 'Column' }, { key: 'type', label: 'Type' }, { key: 'masked', label: 'Values', render: (r) => r.masked ? 'Masked' : 'Visible' }]} /></Section></div>}
    </>}
  </div>
}
