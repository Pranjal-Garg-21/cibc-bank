import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, Printer, ShieldAlert, ThumbsUp, X } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '@/api/client'
import type { CaseResponse, Reasons } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, KeyValues, Loading, NotConnected, PageHeader, Section, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cad, cell, humanize, isoDate, pct } from '@/lib/format'
import { ACTION_LABEL, APPROVAL_LABEL, STATE_LABEL, STATE_TONE, label } from '@/lib/labels'
import { cn } from '@/lib/utils'

type Decision = 'accepted' | 'overridden' | 'approved' | 'rejected'

export function DecisionCard() {
  const me = useMe()
  const { recId = '' } = useParams()
  const q = useQuery({
    queryKey: ['case', recId],
    queryFn: () => api.get<CaseResponse>(`/api/cases/${encodeURIComponent(recId)}`),
    enabled: me.features.queue,
  })
  return (
    <>
      <Button asChild variant="ghost" size="sm" className="mb-3 -ml-2 text-muted-foreground">
        <Link to={me.can.my_queue ? '/queue' : '/approvals'}>
          <ArrowLeft /> Back to queue
        </Link>
      </Button>
      {!me.features.queue && <NotConnected feature="Decision cards" owner="P3" />}
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && <CardView data={q.data} />}
    </>
  )
}

function CardView({ data }: { data: CaseResponse }) {
  const rec = data.recommendation
  const card = rec.card ?? {}
  const state = rec.status?.state as string | undefined
  return (
    <div className="pb-28">
      <PageHeader
        eyebrow={`Decision card · Case ${rec.case_id}`}
        title={label(ACTION_LABEL, rec.action)}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs">{rec.recommendation_id}</span>
            <span>· Business date {isoDate(rec.as_of_date)}</span>
            {state && <StatusBadge tone={STATE_TONE[state] ?? 'neutral'}>{label(STATE_LABEL, state)}</StatusBadge>}
            {rec.requires_approval && <StatusBadge tone="info">Approval: {label(APPROVAL_LABEL, rec.approval_reason)}</StatusBadge>}
          </span>
        }
        actions={
          <Button variant="outline" className="no-print" onClick={() => window.print()}>
            <Printer /> Print
          </Button>
        }
      />
      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <div className="space-y-6">
          <Section title="Recommendation">
            {card.summary && <p className="mb-4 text-[15px] leading-relaxed text-foreground">{card.summary}</p>}
            <KeyValues
              columns={2}
              items={[
                ['Channel', cell(card.channel ?? rec.channel)],
                ['Risk of non-payment', pct(card.risk_score ?? rec.risk_score, 0)],
                ['Expected value', cad(rec.expected_value)],
                ['Runner-up action', label(ACTION_LABEL, card.runner_up_action ?? null)],
                ...(card.contact_window ? [['Contact window', card.contact_window] as [string, string]] : []),
                ['Model version', <span className="font-mono text-xs">{rec.model_version ?? '—'}</span>],
              ]}
            />
            {card.why_beat_runner_up && (
              <p className="mt-4 border-l-2 border-primary pl-3 text-sm text-muted-foreground">{card.why_beat_runner_up}</p>
            )}
          </Section>

          {!!card.top_reasons?.length && (
            <Section title="Main reasons" description="From the risk model (SHAP contributions)">
              <ol className="divide-y divide-border">
                {card.top_reasons.map((r, i) => (
                  <li key={r.feature} className="flex items-start gap-3 py-2.5 text-sm first:pt-0 last:pb-0">
                    <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-secondary text-xs text-muted-foreground">{i + 1}</span>
                    <span className="text-foreground">{r.text}</span>
                    <StatusBadge tone={r.contribution > 0 ? 'warn' : 'ok'}>{r.contribution > 0 ? 'Raises risk' : 'Lowers risk'}</StatusBadge>
                  </li>
                ))}
              </ol>
            </Section>
          )}

          {!!card.blocked_actions?.length && (
            <Section title="Actions ruled out" description="Hard rules applied before the model">
              <DataTable
                data={card.blocked_actions.map((b) => ({ action: label(ACTION_LABEL, b.action), rule: humanize(b.rule) }))}
                caption="Actions ruled out"
                columns={[{ key: 'action', label: 'Action' }, { key: 'rule', label: 'Ruled out by' }]}
              />
            </Section>
          )}

          {!!card.hardship_options?.length && (
            <Section title="Hardship options">
              <DataTable data={card.hardship_options} caption="Hardship options" />
            </Section>
          )}

          {!!card.evidence?.length && (
            <Section title="Evidence from notes and calls" description="Extracted by AI; check before relying on it">
              <ul className="space-y-3 text-sm">
                {card.evidence.map((e) => (
                  <li key={e.source_id} className="border-l-2 border-info/60 pl-3">
                    <p className="text-foreground">“{e.quote}”</p>
                    <p className="text-xs text-faint">
                      {e.source_id} · {isoDate(e.source_ts)}
                    </p>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>

        <div className="space-y-6">
          <Section title="Account and case">
            <KeyValues items={Object.entries(data.context.facts).map(([k, v]) => [humanize(k), cell(v, k)])} />
          </Section>
          <Section title="Recent contacts">
            <DataTable data={data.context.contacts} empty="No recent contacts." caption="Recent contacts" />
          </Section>
          <Section title="Promises to pay">
            <DataTable data={data.context.promises} empty="No promises on record." caption="Promises to pay" />
          </Section>
          <Section title="Decision history" description="Append-only; the latest decision of each kind applies">
            <DataTable data={rec.history} empty="No decisions yet." caption="Decision history" />
          </Section>
        </div>
      </div>
      <ActionBar data={data} />
    </div>
  )
}

function ActionBar({ data }: { data: CaseResponse }) {
  const me = useMe()
  const qc = useQueryClient()
  const rec = data.recommendation
  const reasons = useQuery({ queryKey: ['reasons'], queryFn: () => api.get<Reasons>('/api/review/reasons') })
  const [mode, setMode] = useState<'overridden' | 'rejected' | null>(null)
  const [reason, setReason] = useState('')
  const decide = useMutation({
    mutationFn: (d: { decision: Decision; reason_code?: string }) => api.post(`/api/review/${encodeURIComponent(rec.recommendation_id)}`, d),
    onSuccess: () => {
      setMode(null)
      setReason('')
      void qc.invalidateQueries({ queryKey: ['case', rec.recommendation_id] })
      void qc.invalidateQueries({ queryKey: ['queue'] })
      void qc.invalidateQueries({ queryKey: ['digest'] })
    },
  })
  const { agent_decision, approval } = data.allowed
  if (!agent_decision && !approval) return null
  const status = rec.status ?? {}
  const safeguard = (code: string) => reasons.data?.safeguard_reasons.includes(code)

  return (
    <div className="no-print fixed right-0 bottom-0 left-(--sidebar-width) z-30 border-t border-border bg-popover/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-end gap-3 px-6 py-3">
        <p className="mr-2 self-center text-xs text-muted-foreground">
          Recording as <span className="font-mono text-foreground">{me.agent_id}</span>
        </p>
        {mode ? (
          <form
            className="flex flex-1 flex-wrap items-end gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              if (reason) decide.mutate({ decision: mode, reason_code: reason })
            }}
          >
            <div className="min-w-80">
              <Label htmlFor="reason" className="mb-1.5 text-xs">
                {mode === 'overridden' ? 'Override reason' : 'Rejection reason'} (required)
              </Label>
              <Select value={reason} onValueChange={setReason}>
                <SelectTrigger id="reason" className="w-full">
                  <SelectValue placeholder="Choose a reason…" />
                </SelectTrigger>
                <SelectContent>
                  {reasons.data?.reasons.map((r) => (
                    <SelectItem key={r.code} value={r.code}>
                      {r.label}
                      {safeguard(r.code) && <ShieldAlert className="text-warn" />}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button type="submit" variant="destructive" disabled={!reason || decide.isPending}>
              <Check /> {mode === 'overridden' ? 'Confirm override' : 'Confirm rejection'}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setMode(null)}>
              Cancel
            </Button>
          </form>
        ) : (
          <div className="flex flex-1 flex-wrap items-center gap-3">
            {agent_decision && (
              <>
                <Button size="lg" disabled={decide.isPending} onClick={() => decide.mutate({ decision: 'accepted' })}>
                  <ThumbsUp /> Accept
                </Button>
                <Button size="lg" variant="outline" disabled={decide.isPending} onClick={() => setMode('overridden')}>
                  Override…
                </Button>
                {status.agent && !['pending', 'not_needed'].includes(String(status.agent)) && (
                  <span className="text-xs text-muted-foreground">Your decision: {String(status.agent)} (a new one replaces it)</span>
                )}
              </>
            )}
            {approval && (
              <>
                <Button size="lg" disabled={decide.isPending} onClick={() => decide.mutate({ decision: 'approved' })}>
                  <Check /> Approve
                </Button>
                <Button size="lg" variant="destructive" disabled={decide.isPending} onClick={() => setMode('rejected')}>
                  <X /> Reject…
                </Button>
                {status.approval && !['pending', 'not_needed'].includes(String(status.approval)) && (
                  <span className="text-xs text-muted-foreground">Approval: {String(status.approval)}</span>
                )}
              </>
            )}
          </div>
        )}
        {decide.isSuccess && (
          <span role="status" className={cn('self-center text-sm text-ok')}>
            Saved.
          </span>
        )}
        {decide.error && (
          <div className="w-full">
            <ErrorState error={decide.error} />
          </div>
        )}
      </div>
    </div>
  )
}
