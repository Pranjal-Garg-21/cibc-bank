import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { api, live, qs } from '@/api/client'
import type { DecisionReviewData } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, Empty, ErrorState, Figures, Loading, PageHeader, Section, StatusBadge } from '@/components/app'
import { int, isoDate, pct } from '@/lib/format'
import { cn } from '@/lib/utils'

const PERIODS = [7, 14, 30]

/** Overrides by reason, agent and day. Team leads see their own team; QA analysts see the whole bank. */
export function DecisionReview() {
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const days = PERIODS.includes(Number(params.get('days'))) ? Number(params.get('days')) : 14
  const agent = params.get('agent') ?? ''
  const reason = params.get('reason') ?? ''
  // live: decisions made in other sessions appear within 20 s, or when the window regains focus
  const q = useQuery({ queryKey: ['decision-review', days], queryFn: () => api.poll<DecisionReviewData>(`/api/oversight/decisions${qs({ days })}`), ...live })
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k)
    setParams(next, { replace: true })
  }
  const d = q.data
  const reasonLabel = d?.by_reason.find((r) => r.code === reason)?.label
  const rows = (d?.by_agent ?? []).filter((a) => (!agent || a.agent_id === agent) && (!reason || a.top_reason === reasonLabel))
  const safeguard = d?.by_reason.filter((r) => r.safeguard).reduce((n, r) => n + r.n, 0) ?? 0

  return (
    <>
      <PageHeader
        title="Decision review"
        description={me.role === 'team_lead'
          ? 'How your agents are using accept and override, and why. A high override rate on one reason is worth a conversation.'
          : 'How agents across the bank use accept and override, why, and where the pattern looks unusual. Aggregates only.'}
        actions={
          <div role="group" aria-label="Period" className="no-print inline-flex rounded-sm border border-border bg-white p-0.5">
            {PERIODS.map((p) => (
              <button key={p} type="button" aria-pressed={p === days} onClick={() => set({ days: p === 14 ? null : String(p) })}
                className={cn('min-h-9 rounded-sm px-3 text-sm', p === days ? 'bg-brand font-bold text-white' : 'text-muted-foreground hover:text-brand')}>
                {p} days
              </button>
            ))}
          </div>
        }
      />
      {q.isLoading && <Loading rows={6} />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <div className="space-y-5">
          <Figures items={[
            { label: 'Agent decisions', value: int(d.totals.decisions), note: `${isoDate(d.from)} to ${isoDate(d.to)}` },
            { label: 'Override rate', value: d.totals.override_rate === null ? '–' : pct(d.totals.override_rate, 1), note: `${int(d.totals.overridden)} overridden` },
            { label: 'Safeguard overrides', value: int(safeguard), tone: safeguard ? 'warn' : 'ok', note: 'Hardship, vulnerability or dispute' },
            { label: 'Approvals given', value: int(d.totals.approved), note: `${int(d.totals.rejected)} rejected` },
          ]} />

          {(agent || reason) && (
            <div className="flex flex-wrap items-center gap-2 text-sm" aria-label="Active filters">
              <span className="text-muted-foreground">Filtered by:</span>
              {agent && <Chip onClear={() => set({ agent: null })}>{`Agent ${agent}`}</Chip>}
              {reason && <Chip onClear={() => set({ reason: null })}>{`Top reason: ${reasonLabel ?? reason}`}</Chip>}
            </div>
          )}

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <Section title="Decisions per day" description="Dark segment = overridden">
              <DayChart days={d.by_day} />
            </Section>
            <Section title="Why agents override" description="Select a reason to see who uses it most">
              {d.by_reason.length === 0 ? <p className="text-sm text-muted-foreground">No overrides in this period.</p> : (
                <ul className="space-y-2.5">
                  {d.by_reason.map((r) => (
                    <li key={r.code}>
                      <button type="button" aria-pressed={reason === r.code} onClick={() => set({ reason: reason === r.code ? null : r.code })} className="group block w-full text-left">
                        <span className="flex justify-between gap-3 text-sm">
                          <span className={cn(reason === r.code && 'font-bold text-brand-dark')}>
                            {r.label} {r.safeguard && <StatusBadge tone="warn" title="The customer may need more care than the card assumed">Duty of care</StatusBadge>}
                          </span>
                          <span className="tabular-nums text-muted-foreground">{int(r.n)}</span>
                        </span>
                        <span className="mt-1 block h-2 rounded-sm bg-[#efefef]" aria-hidden="true">
                          <span className={cn('block h-2 rounded-sm group-hover:bg-brand-dark', reason === r.code ? 'bg-brand-dark' : 'bg-brand')} style={{ width: `${Math.max(3, (r.n / d.by_reason[0].n) * 100)}%` }} />
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </div>

          <Section title={d.scope === 'team' ? 'Your agents' : 'Agents'} description="Sorted by override rate. “Review” marks a rate far above the average." flush>
            <DataTable
              data={rows as unknown as Record<string, unknown>[]}
              caption="Override rate by agent"
              sortable
              empty="No decisions match."
              columns={[
                { key: 'agent_id', label: 'Staff ID' },
                { key: 'name', label: 'Name' },
                { key: 'decisions', label: 'Decisions', align: 'right' },
                { key: 'overrides', label: 'Overridden', align: 'right' },
                { key: 'rate', label: 'Override rate', align: 'right', render: (r) => (
                  <span className="inline-flex items-center justify-end gap-2">{pct(r.rate, 0)}{r.outlier ? <StatusBadge tone="warn">Review</StatusBadge> : null}</span>
                ) },
                { key: 'top_reason', label: 'Most common reason', wrap: true },
              ]}
              onRowClick={(r) => set({ agent: agent === r.agent_id ? null : String(r.agent_id) })}
            />
          </Section>

          {d.rejections.length > 0 && (
            <Section title="Why approvals were rejected" flush>
              <DataTable data={d.rejections} caption="Rejections by reason" columns={[{ key: 'label', label: 'Reason' }, { key: 'n', label: 'Rejections', align: 'right' }]} />
            </Section>
          )}
          {!d.totals.decisions && <Empty>No agent decisions in this period.</Empty>}
        </div>
      )}
    </>
  )
}

function Chip({ children, onClear }: { children: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-brand bg-[#f5e3e8] py-0.5 pr-1 pl-2 text-brand-dark">
      {children}
      <button type="button" onClick={onClear} aria-label={`Remove filter ${children}`} className="rounded-sm p-0.5 hover:bg-white"><X className="size-3.5" aria-hidden="true" /></button>
    </span>
  )
}

/** One column per day: grey = accepted, red = overridden. Each column reads out its numbers. */
function DayChart({ days }: { days: DecisionReviewData['by_day'] }) {
  const max = Math.max(1, ...days.map((d) => d.decisions))
  const W = 100 / days.length
  return (
    <figure>
      <svg viewBox="0 0 100 44" className="h-64 w-full" role="img" aria-label="Decisions per day, with overrides marked">
        <line x1="0" y1="40" x2="100" y2="40" stroke="var(--color-border)" strokeWidth="0.3" />
        {days.map((d, i) => {
          const h = (d.decisions / max) * 36
          const o = (d.overrides / max) * 36
          const x = i * W + W * 0.15
          return (
            <g key={d.day}>
              <title>{`${isoDate(d.day)}: ${d.decisions} decisions, ${d.overrides} overridden`}</title>
              <rect x={x} y={40 - h} width={W * 0.7} height={h} fill="#c9cbcd" rx="0.4" />
              {o > 0 && <rect x={x} y={40 - o} width={W * 0.7} height={o} fill="var(--color-brand)" rx="0.4" />}
            </g>
          )
        })}
      </svg>
      <figcaption className="mt-1 flex justify-between text-xs text-faint"><span>{isoDate(days[0]?.day)}</span><span>{isoDate(days[days.length - 1]?.day)}</span></figcaption>
    </figure>
  )
}
