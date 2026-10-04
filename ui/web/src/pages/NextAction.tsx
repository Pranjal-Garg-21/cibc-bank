import { useQuery } from '@tanstack/react-query'
import { Clock3, Mail, MessageSquare, Phone, Search, Send, Smartphone, Target, type LucideIcon } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, live, qs } from '@/api/client'
import { Button } from '@/components/ui/button'
import type { NextActionCustomer, NextActionRecommendation, NextActionSearch } from '@/api/types'
import { useMe } from '@/auth'
import { Empty, ErrorState, Loading, PageHeader, Section, StatusBadge } from '@/components/app'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cad, humanize, int, isoDate, pct } from '@/lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, BATCH_EXPLAINED, CHANNEL_LABEL, STATE_LABEL, STATE_TONE, SUPPORT_LABEL, label } from '@/lib/labels'

const PAGE_SIZE = 50
import { cn } from '@/lib/utils'

const CHANNEL_ICON: Record<string, LucideIcon> = { phone: Phone, sms: MessageSquare, email: Mail, mail: Send, push: Smartphone }

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

export function NextAction() {
  const me = useMe()
  const [params, setParams] = useSearchParams()
  const selected = params.get('customer')
  const [text, setText] = useState('')
  const term = useDebounced(text.trim())
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [term])

  // every customer the role may see, a page at a time (the list used to stop at the first 20)
  const search = useQuery({
    queryKey: ['next-action', 'search', term, page],
    queryFn: () => api.get<NextActionSearch>(`/api/next-action/customers${qs({ q: term, page, page_size: PAGE_SIZE })}`),
    placeholderData: (prev) => prev,
  })
  const pages = search.data ? Math.max(1, Math.ceil(search.data.total / PAGE_SIZE)) : 1
  const detail = useQuery({
    queryKey: ['next-action', 'customer', selected],
    queryFn: () => api.poll<NextActionCustomer>(`/api/next-action/customers/${encodeURIComponent(selected!)}`),
    enabled: !!selected,
    ...live, // an approval given elsewhere shows here within 20 s
  })

  return (
    <>
      <PageHeader
        title="Next best action"
        description={
          me.role === 'hardship_specialist'
            ? "Find a hardship or vulnerability customer to see today's recommended treatment, channel and timing."
            : me.can.my_queue
              ? "Find one of your customers to see today's recommended treatment, channel and timing."
              : "Find any customer to see today's recommended treatment, channel and timing."
        }
      />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <Section title="Find a customer" flush>
          <form className="border-b border-border p-4" role="search" onSubmit={(e) => e.preventDefault()}>
            <Label htmlFor="customer-search" className="mb-1.5 text-[14px] font-bold">Customer ID</Label>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                id="customer-search"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="e.g. G-0100001"
                autoComplete="off"
                spellCheck={false}
                maxLength={40}
                className="h-10 rounded-sm pl-8"
              />
            </div>
          </form>
          {search.isLoading && <div className="p-4"><Loading rows={3} /></div>}
          {search.error && <div className="p-4"><ErrorState error={search.error} onRetry={() => search.refetch()} /></div>}
          {search.data && (search.data.customers.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">
              {term ? `No customer matching “${term}” has a recommendation today.` : 'No recommendations today.'}
            </p>
          ) : (
            <ul aria-label="Matching customers" className="max-h-[32rem] overflow-y-auto">
              {search.data.customers.map((c) => (
                <li key={c.customer_id} className="border-b border-[#e6e6e6] last:border-b-0">
                  <button
                    type="button"
                    aria-current={c.customer_id === selected ? 'true' : undefined}
                    onClick={() => setParams({ customer: c.customer_id })}
                    className={cn(
                      'flex w-full items-center justify-between gap-3 border-l-4 px-4 py-3 text-left hover:bg-panel',
                      c.customer_id === selected ? 'border-l-brand bg-panel' : 'border-l-transparent',
                    )}
                  >
                    <span>
                      <span className="block font-mono text-[14px] text-foreground">{c.customer_id}</span>
                      <span className="block text-xs text-muted-foreground">
                        {c.cases} {c.cases === 1 ? 'case' : 'cases'}
                      </span>
                    </span>
                    <StatusBadge tone={c.action === 'wait' ? 'neutral' : 'accent'}>{label(ACTION_LABEL, c.action)}</StatusBadge>
                  </button>
                </li>
              ))}
            </ul>
          ))}
          {search.data && search.data.total > 0 && (
            <nav aria-label="Customer list pages" className="flex items-center justify-between gap-2 border-t border-border px-4 py-3 text-[13px] text-muted-foreground">
              <span role="status">
                {int((page - 1) * PAGE_SIZE + 1)}–{int(Math.min(page * PAGE_SIZE, search.data.total))} of {int(search.data.total)}
              </span>
              <span className="flex gap-2">
                <Button size="sm" variant="outline" disabled={page <= 1 || search.isFetching} onClick={() => setPage((n) => n - 1)}>Previous</Button>
                <Button size="sm" variant="outline" disabled={page >= pages || search.isFetching} onClick={() => setPage((n) => n + 1)}>Next</Button>
              </span>
            </nav>
          )}
        </Section>

        <div>
          {!selected && (
            <Section>
              <Empty>Search for a customer ID and select it to see the recommended treatment, channel and timing.</Empty>
            </Section>
          )}
          {selected && detail.isLoading && <Loading rows={6} />}
          {selected && detail.error && <ErrorState error={detail.error} onRetry={() => detail.refetch()} />}
          {selected && detail.data && <CustomerView key={detail.data.customer_id} data={detail.data} />}
        </div>
      </div>
    </>
  )
}

function CustomerView({ data }: { data: NextActionCustomer }) {
  const recs = data.recommendations
  const [recId, setRecId] = useState(recs[0].recommendation_id)
  const rec = recs.find((r) => r.recommendation_id === recId) ?? recs[0]

  return (
    <div className="space-y-5">
      <section className="border border-border border-t-4 border-t-brand bg-white px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[13px] text-muted-foreground">Customer</p>
            <h2 className="font-mono text-[24px] leading-tight text-foreground">{data.customer_id}</h2>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted-foreground">
              <span>Case {rec.case_id}</span>
              <span>Business date {isoDate(data.as_of_date)}</span>
              {rec.agent_id && <span>Assigned to {rec.agent_id}</span>}
            </p>
          </div>
          <dl className="flex gap-6 text-right">
            <div>
              <dt className="text-[13px] text-muted-foreground">Risk of non-payment</dt>
              <dd className="text-[22px] tabular-nums">{pct(rec.card.risk_score ?? rec.risk_score, 0)}</dd>
            </div>
            <div>
              <dt className="text-[13px] text-muted-foreground">Expected value</dt>
              <dd className="text-[22px] tabular-nums">{cad(rec.expected_value)}</dd>
            </div>
          </dl>
        </div>
        {recs.length > 1 && (
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3" role="group" aria-label="Cases for this customer">
            <span className="mr-1 text-[13px] text-muted-foreground">Cases:</span>
            {recs.map((r, i) => (
              <button
                key={r.recommendation_id}
                type="button"
                aria-pressed={r.recommendation_id === rec.recommendation_id}
                onClick={() => setRecId(r.recommendation_id)}
                className={cn(
                  'rounded-sm border px-2.5 py-1 text-[13px]',
                  r.recommendation_id === rec.recommendation_id ? 'border-brand bg-[#f5e3e8] text-brand-dark' : 'border-border hover:border-[#8a8d90]',
                )}
              >
                {r.case_id} · {label(ACTION_LABEL, r.action)}{i === 0 ? ' (today’s contact)' : ''}
              </button>
            ))}
          </div>
        )}
      </section>

      {rec.card.summary && <p className="text-[15px] leading-relaxed">{rec.card.summary}</p>}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Treatment rec={rec} />
        <Channel rec={rec} />
        <Timing rec={rec} asOf={data.as_of_date} />
      </div>

      <p className="text-sm">
        <Link to={`/cases/${encodeURIComponent(rec.recommendation_id)}`}>Open the full decision card ›</Link>
      </p>
    </div>
  )
}

function Pillar({ step, title, icon: Icon, headline, sub, children }: { step: number; title: string; icon: LucideIcon; headline: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <section className="flex flex-col border border-border bg-white">
      <header className="flex items-center gap-2 border-b border-border px-5 py-3 text-[13px] font-bold tracking-wide text-muted-foreground uppercase">
        <span className="flex size-5 items-center justify-center rounded-full bg-charcoal text-[11px] text-white">{step}</span>
        {title}
      </header>
      <div className="flex flex-1 flex-col gap-4 p-5">
        <div className="flex items-start gap-3">
          <Icon className="mt-1 size-6 shrink-0 text-brand" strokeWidth={1.5} aria-hidden="true" />
          <div>
            <p className="text-[20px] leading-snug text-foreground">{headline}</p>
            {sub && <p className="mt-1 text-[13px] text-muted-foreground">{sub}</p>}
          </div>
        </div>
        {children}
      </div>
    </section>
  )
}

function Detail({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-[#e6e6e6] pt-3 text-sm">
      <p className="mb-1 text-[13px] text-muted-foreground">{title}</p>
      {children}
    </div>
  )
}

function Treatment({ rec }: { rec: NextActionRecommendation }) {
  const card = rec.card
  return (
    <Pillar step={1} title="Right treatment" icon={Target} headline={label(ACTION_LABEL, rec.action)}
      sub={card.support_reason ? label(SUPPORT_LABEL, card.support_reason) : undefined}>
      {card.runner_up_action && (
        <Detail title="Next-best alternative">
          <p>{label(ACTION_LABEL, card.runner_up_action)}</p>
          {card.why_beat_runner_up && <p className="mt-1 text-muted-foreground">{card.why_beat_runner_up}</p>}
        </Detail>
      )}
      {!!card.hardship_options?.length && (
        <Detail title="Hardship options to offer first">
          <ul className="list-disc space-y-0.5 pl-5">
            {card.hardship_options.map((o, i) => <li key={i}>{String(o.name ?? o.program_id ?? Object.values(o)[0] ?? '-')}</li>)}
          </ul>
        </Detail>
      )}
      {!!card.top_reasons?.length && (
        <Detail title="Why">
          <ul className="space-y-1">
            {card.top_reasons.map((r) => (
              <li key={r.feature}>
                {r.text} <span className="text-muted-foreground">({r.contribution > 0 ? 'raises' : 'lowers'} risk)</span>
              </li>
            ))}
          </ul>
        </Detail>
      )}
    </Pillar>
  )
}

function Channel({ rec }: { rec: NextActionRecommendation }) {
  const ruledOut = rec.card.blocked_actions ?? []
  return (
    <Pillar step={2} title="Channel" icon={rec.channel ? (CHANNEL_ICON[rec.channel] ?? Send) : Clock3}
      headline={rec.channel ? label(CHANNEL_LABEL, rec.channel) : 'No contact'}
      sub={rec.channel ? `Via ${label(ACTION_LABEL, rec.action).toLowerCase()}` : 'No channel is used today'}>
      <Detail title="Ruled out today">
        {ruledOut.length ? (
          <ul className="space-y-1">
            {ruledOut.map((b) => (
              <li key={`${b.action}-${b.rule}`} className="flex flex-wrap items-baseline justify-between gap-2">
                <span>{label(ACTION_LABEL, b.action)}</span>
                <span className="text-xs text-muted-foreground">{humanize(b.rule)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">Nothing ruled out by policy.</p>
        )}
      </Detail>
    </Pillar>
  )
}

function Timing({ rec, asOf }: { rec: NextActionRecommendation; asOf: string }) {
  const card = rec.card
  const day = isoDate(asOf)
  const [headline, sub] =
    rec.action === 'wait'
      ? ['Not today', 'Wait: no contact on this case today']
      : card.contact_window
        ? [card.contact_window.replace(/\s*customer-local$/, ''), `Today, ${day}, in the customer's local time`]
        : rec.channel === 'mail'
          ? ['Post today', `${day}; no time-of-day limit for letters`]
          : ['Any time today', `${day}; delivered, not a live contact`]
  return (
    <Pillar step={3} title="Timing" icon={Clock3} headline={headline} sub={sub}>
      <Detail title="Where it stands">
        <ApprovalState rec={rec} />
      </Detail>
    </Pillar>
  )
}

/** The live state (gold.nba_overrides), not the static requires_approval flag: once a specialist or team lead has
 * approved the case it reads "Approved", then "Cleared" or "Done". */
function ApprovalState({ rec }: { rec: NextActionRecommendation }) {
  const st = rec.status ?? { state: null, approval: rec.requires_approval ? 'pending' : 'not_needed', agent: null, completed: false, outcome: null }
  const kind = label(APPROVAL_LABEL, rec.approval_reason).toLowerCase()
  const state = st.state ? <StatusBadge tone={STATE_TONE[st.state] ?? 'neutral'}>{label(STATE_LABEL, st.state)}</StatusBadge> : null
  const note =
    st.state === 'overridden' ? 'The assigned agent overrode it: it will not be carried out today.'
      : st.state === 'blocked' ? `Rejected at ${kind} approval: it will not be carried out today.`
        : st.state === 'done' ? (st.completed ? 'The call has been made and marked complete.' : 'The agent accepted waiting today.')
          : st.approval === 'pending'
            ? rec.approval_reason === 'digital_batch' ? `Waiting for the team lead to approve today's batch. ${BATCH_EXPLAINED}` : `Waiting for ${kind} approval.`
            : st.state === 'waiting_agent' ? `${st.approval === 'approved' ? `Approved (${kind}). ` : ''}Waiting for the assigned agent to accept it.`
              : st.approval === 'approved' ? `Approved (${kind}). Cleared to carry out.`
                : st.state === 'ready' ? 'No approval needed. Cleared to carry out.' : 'No approval needed.'
  return (
    <p>
      {state} <span className="text-muted-foreground">{note}</span>
    </p>
  )
}
