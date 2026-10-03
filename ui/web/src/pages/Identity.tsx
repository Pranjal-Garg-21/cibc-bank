import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Bot, ChevronLeft, ChevronRight, HelpCircle, UserCheck, UserX } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, qs } from '@/api/client'
import type { IdentityPair, IdentityQueue, RecordField } from '@/api/types'
import { DataTable, ErrorState, Loading, PageHeader, Section, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { int, pct } from '@/lib/format'
import { IDENTITY_REASON, label } from '@/lib/labels'
import { cn } from '@/lib/utils'

const PAGE = 50

export function IdentityQueuePage() {
  const nav = useNavigate()
  const [reason, setReason] = useState('')
  const [page, setPage] = useState(0)
  const q = useQuery({
    queryKey: ['identity', reason, page],
    queryFn: () => api.get<IdentityQueue>(`/api/identity/queue${qs({ reason, limit: PAGE, offset: page * PAGE })}`),
    placeholderData: (prev) => prev,
  })
  const d = q.data
  return (
    <>
      <PageHeader
        eyebrow="Data governance"
        title="Identity review"
        description="Customer record pairs that automated matching could not settle. Your decisions are applied on the next identity run."
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            {Object.entries(d.counts_by_reason).map(([k, n]) => (
              <button
                key={k}
                type="button"
                aria-pressed={reason === k}
                onClick={() => {
                  setReason(reason === k ? '' : k)
                  setPage(0)
                }}
                className="text-left"
              >
                <Card className={cn('gap-1 py-4 transition-colors hover:bg-accent', reason === k && 'ring-2 ring-primary')}>
                  <CardContent className="px-4">
                    <p className="text-[11px] font-medium tracking-[0.06em] text-muted-foreground uppercase">{label(IDENTITY_REASON, k)}</p>
                    <p className="mt-1.5 text-2xl font-semibold tabular-nums">{int(n)}</p>
                  </CardContent>
                </Card>
              </button>
            ))}
          </div>
          <Section
            title={`Open pairs${reason ? ` · ${label(IDENTITY_REASON, reason)}` : ''}`}
            description={`${int(d.total)} to review, highest match probability first`}
            actions={
              <div className="flex items-center gap-1">
                <Button variant="ghost" size="icon-sm" aria-label="Previous page" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                  <ChevronLeft />
                </Button>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {int(page * PAGE + 1)}–{int(Math.min((page + 1) * PAGE, d.total))}
                </span>
                <Button variant="ghost" size="icon-sm" aria-label="Next page" disabled={(page + 1) * PAGE >= d.total} onClick={() => setPage((p) => p + 1)}>
                  <ChevronRight />
                </Button>
              </div>
            }
          >
            <DataTable
              data={d.items as unknown as Record<string, unknown>[]}
              rowKey="pair_id"
              empty="No open pairs."
              caption="Open identity pairs"
              onRowClick={(r) => nav(`/identity/${r.pair_id}`)}
              columns={[
                { key: 'reason', label: 'Why it needs review', render: (r) => label(IDENTITY_REASON, r.reason as string) },
                { key: 'sources', label: 'Source systems', render: (r) => <span className="font-mono text-xs uppercase">{`${r.source_a} ↔ ${r.source_b}`}</span> },
                { key: 'splink_probability', label: 'Match probability', align: 'right', render: (r) => pct(r.splink_probability, 2) },
                {
                  key: 'llm',
                  label: 'AI second opinion',
                  render: (r) =>
                    !r.llm_available ? (
                      <span className="text-faint">None</span>
                    ) : r.llm_match ? (
                      <StatusBadge tone="info">Same person</StatusBadge>
                    ) : (
                      <StatusBadge>Different people</StatusBadge>
                    ),
                },
              ]}
            />
          </Section>
        </div>
      )}
    </>
  )
}

export function IdentityPairPage() {
  const { pairId = '' } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['pair', pairId], queryFn: () => api.get<IdentityPair>(`/api/identity/pairs/${pairId}`) })
  const [note, setNote] = useState('')
  const decide = useMutation({
    mutationFn: (decision: string) => api.post('/api/identity/decisions', { pair_id: pairId, decision, note }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['identity'] })
      nav('/identity')
    },
  })
  const p = q.data
  return (
    <>
      <Button asChild variant="ghost" size="sm" className="mb-3 -ml-2 text-muted-foreground">
        <Link to="/identity">
          <ArrowLeft /> Back to identity review
        </Link>
      </Button>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} />}
      {p && (
        <div className="space-y-6">
          <PageHeader
            eyebrow={`Identity review · ${label(IDENTITY_REASON, p.reason)}`}
            title="Are these the same customer?"
            description={p.decision ? <StatusBadge tone="ok">Decided: {p.decision.replace('_', ' ')}</StatusBadge> : 'Compare the two records, then record your decision.'}
          />
          <Section title="Customer records side by side" description="Differences are highlighted. Phone numbers are masked to the last four digits.">
            <Compare a={p.record_a} b={p.record_b} />
          </Section>
          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="Probabilistic match" actions={<StatusBadge tone="accent">{pct(p.splink_probability, 2)}</StatusBadge>}>
              {p.splink_explanation.length ? (
                <dl className="divide-y divide-border text-sm">
                  {p.splink_explanation.map((x) => {
                    const [field, ...rest] = x.split(':')
                    return (
                      <div key={x} className="grid grid-cols-[7rem_1fr] gap-3 py-2 first:pt-0 last:pb-0">
                        <dt className="text-muted-foreground capitalize">{field}</dt>
                        <dd className="text-foreground">{rest.join(':').trim()}</dd>
                      </div>
                    )
                  })}
                </dl>
              ) : (
                <p className="text-sm text-muted-foreground">No probabilistic comparison for this pair.</p>
              )}
            </Section>
            <Section title="AI second opinion" actions={<Bot className="size-4 text-faint" />}>
              {p.llm.available ? (
                <>
                  <div className="flex items-center gap-2">
                    <StatusBadge tone="info">{p.llm.match ? 'Same person' : 'Different people'}</StatusBadge>
                    <span className="text-sm text-muted-foreground">confidence {pct(p.llm.probability)}</span>
                  </div>
                  {p.llm.reason && <p className="mt-3 text-sm whitespace-pre-wrap text-foreground">{p.llm.reason}</p>}
                  <p className="mt-3 text-xs text-faint">Written by a language model. Advisory only.</p>
                </>
              ) : (
                <p className="text-sm text-muted-foreground">No AI opinion is available for this pair.</p>
              )}
            </Section>
          </div>
          <Section title="Your decision" description="Recorded under your staff ID and applied on the next identity run">
            <Label htmlFor="note" className="mb-1.5">
              Note <span className="font-normal text-faint">(optional, up to 500 characters)</span>
            </Label>
            <Textarea id="note" rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="mt-4 flex flex-wrap gap-3">
              <Button size="lg" disabled={decide.isPending} onClick={() => decide.mutate('match')}>
                <UserCheck /> Same customer
              </Button>
              <Button size="lg" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate('no_match')}>
                <UserX /> Different customers
              </Button>
              <Button size="lg" variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate('unresolved')}>
                <HelpCircle /> Can't tell
              </Button>
            </div>
            {decide.error && (
              <div className="mt-3">
                <ErrorState error={decide.error} />
              </div>
            )}
          </Section>
        </div>
      )}
    </>
  )
}

function Compare({ a, b }: { a: RecordField[]; b: RecordField[] }) {
  const fields = [...new Set([...a, ...b].map((f) => f.field))]
  const get = (r: RecordField[], f: string) => r.find((x) => x.field === f)
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <Table>
        <TableHeader className="bg-secondary/60">
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-44 text-[11px] font-semibold tracking-[0.05em] text-muted-foreground uppercase">Field</TableHead>
            <TableHead className="text-[11px] font-semibold tracking-[0.05em] text-muted-foreground uppercase">Record A</TableHead>
            <TableHead className="text-[11px] font-semibold tracking-[0.05em] text-muted-foreground uppercase">Record B</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {fields.map((f) => {
            const va = get(a, f)?.value ?? ''
            const vb = get(b, f)?.value ?? ''
            const differs = f !== 'src' && va !== vb
            return (
              <TableRow key={f} className={cn(differs && 'bg-warn/5')}>
                <TableCell className="text-muted-foreground">{get(a, f)?.label ?? get(b, f)?.label}</TableCell>
                {[va, vb].map((v, i) => (
                  <TableCell key={i} className={cn('font-mono text-[13px]', differs ? 'text-warn' : 'text-foreground', f === 'src' && 'uppercase')}>
                    {v || <span className="text-faint">—</span>}
                  </TableCell>
                ))}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
