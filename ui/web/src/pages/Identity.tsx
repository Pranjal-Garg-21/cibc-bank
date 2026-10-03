import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, qs } from '@/api/client'
import type { IdentityPair, IdentityQueue, RecordField } from '@/api/types'
import { DataTable, ErrorState, Loading, PageHeader, Section, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
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
  const total = d ? Object.values(d.counts_by_reason).reduce((a, b) => a + b, 0) : 0
  return (
    <>
      <PageHeader
        title="Identity review"
        description="Pairs of customer records that automated matching could not settle. Decisions apply on the next identity run."
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
          <nav aria-label="Filter by reason" className="self-start border border-border bg-white">
            <p className="border-b border-border bg-panel px-3 py-2 text-[13px] font-bold">Why it needs review</p>
            <ul className="text-sm">
              {[['', 'All open pairs', total] as const, ...Object.entries(d.counts_by_reason).map(([k, n]) => [k, label(IDENTITY_REASON, k), n] as const)].map(
                ([k, text, n]) => (
                  <li key={k || 'all'}>
                    <button
                      type="button"
                      aria-pressed={reason === k}
                      onClick={() => {
                        setReason(k)
                        setPage(0)
                      }}
                      className={cn(
                        'flex w-full justify-between border-l-4 px-3 py-2 text-left hover:bg-[#f7f7f7]',
                        reason === k ? 'border-l-brand bg-[#f7f7f7] font-bold' : 'border-l-transparent',
                      )}
                    >
                      <span>{text}</span>
                      <span className="tabular-nums text-muted-foreground">{int(n)}</span>
                    </button>
                  </li>
                ),
              )}
            </ul>
          </nav>
          <Section
            title={reason ? label(IDENTITY_REASON, reason) : 'All open pairs'}
            description={`${int(d.total)} to review, highest match probability first`}
            flush
            actions={
              <span className="flex items-center gap-2 text-[13px]">
                <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                  Previous
                </Button>
                <span className="text-muted-foreground tabular-nums">
                  {int(d.total ? page * PAGE + 1 : 0)}-{int(Math.min((page + 1) * PAGE, d.total))} of {int(d.total)}
                </span>
                <Button variant="secondary" size="sm" disabled={(page + 1) * PAGE >= d.total} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </span>
            }
          >
            <DataTable
              data={d.items as unknown as Record<string, unknown>[]}
              rowKey="pair_id"
              empty="No open pairs."
              caption="Open identity pairs"
              onRowClick={(r) => nav(`/identity/${r.pair_id}`)}
              columns={[
                { key: 'reason', label: 'Reason', render: (r) => label(IDENTITY_REASON, r.reason as string) },
                { key: 'sources', label: 'Source systems', render: (r) => `${String(r.source_a).toUpperCase()} and ${String(r.source_b).toUpperCase()}` },
                { key: 'splink_probability', label: 'Match probability', align: 'right', render: (r) => pct(r.splink_probability, 2) },
                {
                  key: 'llm',
                  label: 'Automated second opinion',
                  render: (r) => (!r.llm_available ? <span className="text-muted-foreground">None</span> : r.llm_match ? 'Same person' : 'Different people'),
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
      <p className="mb-3 text-sm">
        <Link to="/identity">‹ Back to identity review</Link>
      </p>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} />}
      {p && (
        <div className="space-y-5">
          <PageHeader
            title="Are these the same customer?"
            description={
              <span className="flex items-center gap-3">
                {label(IDENTITY_REASON, p.reason)}
                {p.decision && <StatusBadge tone="ok">Decided: {p.decision.replace('_', ' ')}</StatusBadge>}
              </span>
            }
          />
          <Section title="The two records" description="Fields that differ are marked. Phone numbers show the last four digits only." flush>
            <Compare a={p.record_a} b={p.record_b} />
          </Section>
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <Section title="Probabilistic match" actions={<span className="text-[15px] font-bold tabular-nums">{pct(p.splink_probability, 2)}</span>}>
              {p.splink_explanation.length ? (
                <table className="w-full text-sm">
                  <tbody>
                    {p.splink_explanation.map((x) => {
                      const [field, ...rest] = x.split(':')
                      return (
                        <tr key={x} className="border-b border-[#e6e6e6] last:border-b-0">
                          <th scope="row" className="w-28 py-1.5 text-left font-normal text-muted-foreground capitalize">
                            {field}
                          </th>
                          <td className="py-1.5">{rest.join(':').trim()}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <p className="text-sm text-muted-foreground">No probabilistic comparison for this pair.</p>
              )}
            </Section>
            <Section title="Automated second opinion" description="Language model; advisory only">
              {p.llm.available ? (
                <>
                  <p className="text-sm">
                    <strong>{p.llm.match ? 'Same person' : 'Different people'}</strong>
                    <span className="text-muted-foreground"> (confidence {pct(p.llm.probability)})</span>
                  </p>
                  {p.llm.reason && <p className="mt-2 text-sm whitespace-pre-wrap">{p.llm.reason}</p>}
                </>
              ) : (
                <p className="text-sm text-muted-foreground">None for this pair.</p>
              )}
            </Section>
          </div>
          <Section title="Your decision" description="Recorded under your staff ID">
            <Label htmlFor="note" className="mb-1.5 text-[14px] font-bold">
              Note <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Textarea id="note" rows={2} maxLength={500} className="rounded-sm" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="mt-4 flex flex-wrap gap-3">
              <Button size="lg" disabled={decide.isPending} onClick={() => decide.mutate('match')}>
                Same customer
              </Button>
              <Button size="lg" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate('no_match')}>
                Different customers
              </Button>
              <Button size="lg" variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate('unresolved')}>
                Can't tell
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
    <table className="w-full text-sm">
      <thead className="bg-panel">
        <tr className="border-b border-input text-left">
          <th scope="col" className="w-44 px-3 py-2 text-[13px]">
            Field
          </th>
          <th scope="col" className="px-3 py-2 text-[13px]">
            Record A
          </th>
          <th scope="col" className="px-3 py-2 text-[13px]">
            Record B
          </th>
          <th scope="col" className="w-36 px-3 py-2 text-[13px]">
            <span className="sr-only">Comparison</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => {
          const va = get(a, f)?.value ?? ''
          const vb = get(b, f)?.value ?? ''
          const oneMissing = f !== 'src' && (!va || !vb) && va !== vb
          const differs = f !== 'src' && va !== vb && !oneMissing
          return (
            <tr key={f} className={cn('border-b border-[#e6e6e6]', differs && 'bg-[#fdf6f8]')}>
              <th scope="row" className="px-3 py-2 text-left font-normal text-muted-foreground">
                {get(a, f)?.label ?? get(b, f)?.label}
              </th>
              <td className="px-3 py-2 font-mono text-[13px]">{f === 'src' ? va.toUpperCase() : va || '-'}</td>
              <td className="px-3 py-2 font-mono text-[13px]">{f === 'src' ? vb.toUpperCase() : vb || '-'}</td>
              <td className="px-3 py-2 text-xs">
                {differs ? (
                  <span className="font-bold text-danger">Differs</span>
                ) : oneMissing ? (
                  <span className="text-muted-foreground">Missing on one side</span>
                ) : f === 'src' ? (
                  ''
                ) : (
                  <span className="text-ok">Same</span>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
