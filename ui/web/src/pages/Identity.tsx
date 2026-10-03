import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, qs } from '../api/client'
import type { IdentityPair, IdentityQueue, RecordField } from '../api/types'
import { Badge, Button, DataTable, ErrorState, FieldLabel, PageHeader, Panel, Spinner, Stat, cx, inputClass } from '../components/ui'
import { int, pct } from '../lib/format'
import { IDENTITY_REASON, label } from '../lib/labels'

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
        title="Identity review"
        subtitle="Record pairs the matching could not settle. Decisions are applied on the next identity run."
      />
      {q.isLoading && <Spinner />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
            {Object.entries(d.counts_by_reason).map(([k, n]) => (
              <button
                key={k}
                type="button"
                onClick={() => {
                  setReason(reason === k ? '' : k)
                  setPage(0)
                }}
                aria-pressed={reason === k}
                className={cx('rounded-lg text-left', reason === k && 'ring-2 ring-accent-text')}
              >
                <Stat label={label(IDENTITY_REASON, k)} value={int(n)} />
              </button>
            ))}
          </div>
          <DataTable
            data={d.items as unknown as Record<string, unknown>[]}
            rowKey="pair_id"
            empty="No open pairs."
            caption="Open identity pairs"
            onRowClick={(r) => nav(`/identity/${r.pair_id}`)}
            columns={[
              { key: 'reason', label: 'Why it is here', render: (r) => label(IDENTITY_REASON, r.reason as string) },
              { key: 'sources', label: 'Sources', render: (r) => `${r.source_a} ↔ ${r.source_b}` },
              { key: 'splink_probability', label: 'Match probability', align: 'right', render: (r) => pct(r.splink_probability, 2) },
              {
                key: 'llm',
                label: 'AI second opinion',
                render: (r) =>
                  !r.llm_available ? (
                    <span className="text-faint">Unavailable</span>
                  ) : r.llm_match ? (
                    <Badge tone="info">Same person</Badge>
                  ) : (
                    <Badge>Different</Badge>
                  ),
              },
            ]}
          />
          <div className="mt-3 flex items-center justify-between text-sm text-muted">
            <span>
              {int(d.total)} open{reason ? ` · ${label(IDENTITY_REASON, reason)}` : ''}
            </span>
            <div className="flex gap-2">
              <Button variant="ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <Button variant="ghost" disabled={(page + 1) * PAGE >= d.total} onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </div>
          </div>
        </>
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
      <Link to="/identity" className="mb-3 inline-block text-sm text-accent-text hover:underline">
        ← Back to identity review
      </Link>
      {q.isLoading && <Spinner />}
      {q.error && <ErrorState error={q.error} />}
      {p && (
        <>
          <PageHeader
            title="Are these the same person?"
            subtitle={
              <span className="flex items-center gap-2">
                {label(IDENTITY_REASON, p.reason)}
                {p.decision && <Badge tone="ok">Decided: {p.decision.replace('_', ' ')}</Badge>}
              </span>
            }
          />
          <Panel title="Records side by side">
            <Compare a={p.record_a} b={p.record_b} />
            <p className="mt-3 text-xs text-faint">Phone numbers are shown masked to the last four digits.</p>
          </Panel>
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Panel title={`Probabilistic match · ${pct(p.splink_probability, 2)}`}>
              {p.splink_explanation.length ? (
                <ul className="space-y-1 text-sm">
                  {p.splink_explanation.map((x) => {
                    const [field, ...rest] = x.split(':')
                    return (
                      <li key={x} className="flex gap-3">
                        <span className="w-24 shrink-0 text-muted">{field}</span>
                        <span className="text-text">{rest.join(':').trim()}</span>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <p className="text-sm text-muted">No probabilistic comparison for this pair.</p>
              )}
            </Panel>
            <Panel title="AI second opinion">
              {p.llm.available ? (
                <>
                  <p className="text-sm">
                    <Badge tone="info">{p.llm.match ? 'Same person' : 'Different people'}</Badge>
                    <span className="ml-2 text-muted">confidence {pct(p.llm.probability)}</span>
                  </p>
                  {p.llm.reason && <p className="mt-2 text-sm whitespace-pre-wrap text-text">{p.llm.reason}</p>}
                  <p className="mt-2 text-xs text-faint">Written by a language model. Advisory only.</p>
                </>
              ) : (
                <p className="text-sm text-muted">No AI opinion is available for this pair.</p>
              )}
            </Panel>
          </div>
          <Panel title="Your decision" className="mt-6">
            <FieldLabel htmlFor="note" hint="optional, up to 500 characters">
              Note
            </FieldLabel>
            <textarea id="note" rows={2} maxLength={500} className={inputClass} value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="mt-4 flex flex-wrap gap-3">
              <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate('match')}>
                Same person
              </Button>
              <Button variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate('no_match')}>
                Different people
              </Button>
              <Button variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate('unresolved')}>
                Can't tell
              </Button>
            </div>
            {decide.error && (
              <div className="mt-3">
                <ErrorState error={decide.error} />
              </div>
            )}
          </Panel>
        </>
      )}
    </>
  )
}

function Compare({ a, b }: { a: RecordField[]; b: RecordField[] }) {
  const fields = [...new Set([...a, ...b].map((f) => f.field))]
  const get = (r: RecordField[], f: string) => r.find((x) => x.field === f)
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th scope="col" className="w-40 py-2 font-medium">
            Field
          </th>
          <th scope="col" className="py-2 font-medium">
            Record A
          </th>
          <th scope="col" className="py-2 font-medium">
            Record B
          </th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => {
          const va = get(a, f)?.value ?? ''
          const vb = get(b, f)?.value ?? ''
          const differs = f !== 'src' && va !== vb
          return (
            <tr key={f} className="border-t border-border">
              <th scope="row" className="py-2 text-left font-normal text-muted">
                {get(a, f)?.label ?? get(b, f)?.label}
              </th>
              {[va, vb].map((v, i) => (
                <td key={i} className={cx('py-2 font-mono text-[13px]', differs ? 'text-warn' : 'text-text')}>
                  {v || <span className="text-faint">—</span>}
                </td>
              ))}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
