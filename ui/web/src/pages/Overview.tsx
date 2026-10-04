import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import type { Overview as OverviewData, OverviewColumn, OverviewSection, OverviewTile } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, Empty, ErrorState, Loading, PageHeader, Section } from '@/components/app'
import { Button } from '@/components/ui/button'
import { amount, int, isoDate, pct } from '@/lib/format'
import { cn } from '@/lib/utils'

function greeting(name: string | null): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
  const first = name?.split(' ')[0]
  return first ? `${part}, ${first}` : part
}

const TONE = { ok: 'text-ok', warn: 'text-warn', danger: 'text-danger' } as const

/** Every role's first screen: the server describes the page for the signed-in role, this draws it. */
export function Overview() {
  const me = useMe()
  const q = useQuery({ queryKey: ['overview'], queryFn: () => api.get<OverviewData>('/api/overview'), refetchInterval: 60_000 })
  const d = q.data
  return (
    <>
      <PageHeader
        title={greeting(me.name)}
        description={d ? <><span className="font-bold text-foreground">{d.headline}.</span> {d.intro}</> : undefined}
        actions={
          <Button variant="outline" className="no-print" onClick={() => void q.refetch()} disabled={q.isFetching} aria-label="Refresh the overview">
            <RefreshCw className={cn('size-4', q.isFetching && 'animate-spin')} aria-hidden="true" strokeWidth={1.5} />
            {q.isFetching ? 'Refreshing' : 'Refresh'}
          </Button>
        }
      />
      {q.isLoading && <Loading rows={6} />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <div className="space-y-5">
          {d.tiles.length > 0 && <Tiles tiles={d.tiles} />}
          <Sections sections={d.sections} />
          {!d.tiles.length && !d.sections.length && <Section><Empty>{d.intro}</Empty></Section>}
          <p className="text-xs text-faint">Business date {isoDate(d.as_of_date)}. Figures update every minute.</p>
        </div>
      )}
    </>
  )
}

function tileValue(t: OverviewTile): string {
  if (t.value === null) return '–'
  return t.kind === 'percent' ? pct(t.value, 0) : int(t.value)
}

function Tiles({ tiles }: { tiles: OverviewTile[] }) {
  return (
    <ul className="grid grid-cols-1 gap-px border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
      {tiles.map((t) => {
        const inner = (
          <>
            <span className="flex items-start justify-between gap-2 text-[13px] text-muted-foreground">
              {t.label}
              {t.to && <ArrowUpRight className="size-4 shrink-0 text-faint transition-colors group-hover:text-brand" aria-hidden="true" strokeWidth={1.5} />}
            </span>
            <span className={cn('mt-1 block text-[30px] leading-tight tabular-nums', t.tone ? TONE[t.tone] : 'text-foreground')}>{tileValue(t)}</span>
            {t.note && <span className="mt-0.5 block text-xs text-faint">{t.note}</span>}
          </>
        )
        const cls = 'group block h-full bg-white px-5 py-5'
        return (
          <li key={t.id}>
            {t.to ? <Link to={t.to} className={cn(cls, 'no-underline hover:bg-[#fbf3f5] focus-visible:bg-[#fbf3f5]')}>{inner}</Link> : <div className={cls}>{inner}</div>}
          </li>
        )
      })}
    </ul>
  )
}

/** Tables go full width; consecutive bar charts share a row. */
function Sections({ sections }: { sections: OverviewSection[] }) {
  const out: ReactNode[] = []
  let bars: OverviewSection[] = []
  const flush = () => {
    if (bars.length) out.push(<div key={`bars-${bars[0].id}`} className="grid grid-cols-1 gap-5 lg:grid-cols-2">{bars.map((b) => <SectionView key={b.id} s={b} />)}</div>)
    bars = []
  }
  for (const s of sections) {
    if (s.kind === 'bars') bars.push(s)
    else { flush(); out.push(<SectionView key={s.id} s={s} />) }
  }
  flush()
  return <>{out}</>
}

function SectionView({ s }: { s: OverviewSection }) {
  const nav = useNavigate()
  if (s.kind === 'bars') {
    const max = Math.max(1, ...s.items.map((i) => i.value))
    return (
      <Section title={s.title} description={s.description ?? undefined}>
        {s.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing to show yet.</p>
        ) : (
          <ul className="space-y-2.5">
            {s.items.map((i) => {
              const row = (
                <>
                  <span className="flex justify-between gap-3 text-sm">
                    <span className="text-foreground">{i.label}</span>
                    <span className="tabular-nums text-muted-foreground">{int(i.value)}</span>
                  </span>
                  <span className="mt-1 block h-2 rounded-sm bg-[#efefef]" aria-hidden="true">
                    <span className="block h-2 rounded-sm bg-brand transition-all group-hover:bg-brand-dark" style={{ width: `${Math.max(3, (i.value / max) * 100)}%` }} />
                  </span>
                </>
              )
              return <li key={i.label}>{i.to ? <Link to={i.to} className="group block no-underline hover:no-underline focus-visible:outline-2">{row}</Link> : <div>{row}</div>}</li>
            })}
          </ul>
        )}
      </Section>
    )
  }
  const cols = s.columns.map((c: OverviewColumn) => ({
    key: c.key, label: c.label, wrap: c.wrap,
    render: (r: Record<string, unknown>) => (c.kind === 'percent' ? pct(r[c.key], 0) : c.kind === 'money' ? amount(r[c.key]) : c.kind === 'int' ? int(r[c.key]) : (r[c.key] == null || r[c.key] === '' ? '–' : String(r[c.key]))),
  }))
  return (
    <Section title={s.title} description={s.description ?? undefined} flush>
      <DataTable
        data={s.rows}
        columns={cols}
        empty={s.empty}
        caption={s.title}
        sortable
        searchable={s.rows.length > 6}
        onRowClick={s.rows.some((r) => r._to) ? (r) => r._to && nav(String(r._to)) : undefined}
      />
    </Section>
  )
}
