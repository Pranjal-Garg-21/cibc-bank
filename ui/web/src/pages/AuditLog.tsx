import { useQuery } from '@tanstack/react-query'
import { Radio } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, qs } from '@/api/client'
import type { AuditData } from '@/api/types'
import { DataTable, ErrorState, Figures, Loading, PageHeader, Section, StatusBadge } from '@/components/app'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { dateTime, int } from '@/lib/format'
import { cn } from '@/lib/utils'

const WINDOWS = [{ h: 1, label: 'Last hour' }, { h: 24, label: 'Last 24 hours' }, { h: 72, label: 'Last 3 days' }, { h: 168, label: 'Last 7 days' }]
const ROLE = { agent: 'Agent', senior_agent: 'Senior agent', team_lead: 'Team lead', hardship_specialist: 'Hardship specialist', qa_analyst: 'QA analyst' } as Record<string, string>
const SELECT = 'h-10 w-full rounded-sm border border-input bg-white px-2 text-sm focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 outline-none'

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

/** Who did what, and what was refused. QA analysts only. Opening this page is itself recorded. */
export function AuditLog() {
  const [params, setParams] = useSearchParams()
  const hours = Number(params.get('hours')) || 24
  const action = params.get('action') ?? ''
  const outcome = params.get('outcome') ?? ''
  const [user, setUser] = useState(params.get('user') ?? '')
  const [text, setText] = useState(params.get('q') ?? '')
  const [live, setLive] = useState(false)
  const u = useDebounced(user.trim())
  const t = useDebounced(text.trim())
  const q = useQuery({
    queryKey: ['audit', hours, action, outcome, u, t],
    queryFn: () => api.get<AuditData>(`/api/oversight/audit${qs({ hours, action, outcome, user: u, q: t, limit: 300 })}`),
    refetchInterval: live ? 10_000 : false,
    placeholderData: (prev) => prev,
  })
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) v ? next.set(k, v) : next.delete(k)
    setParams(next, { replace: true })
  }
  const d = q.data

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every sign-in, opened case, decision, question and refused request. Opening this page is itself recorded."
        actions={
          <button type="button" aria-pressed={live} onClick={() => setLive((v) => !v)}
            className={cn('inline-flex min-h-9 items-center gap-2 rounded-sm border px-3 text-sm', live ? 'border-brand bg-[#f5e3e8] font-bold text-brand-dark' : 'border-border bg-white text-muted-foreground hover:text-brand')}>
            <Radio className={cn('size-4', live && 'animate-pulse')} aria-hidden="true" strokeWidth={1.5} />{live ? 'Live: every 10 s' : 'Go live'}
          </button>
        }
      />
      <div className="space-y-5">
        <Section>
          <form className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5" onSubmit={(e) => e.preventDefault()} role="search" aria-label="Filter the audit log">
            <div><Label htmlFor="a-window" className="mb-1.5 text-[13px] font-bold">Period</Label>
              <select id="a-window" className={SELECT} value={hours} onChange={(e) => set({ hours: e.target.value === '24' ? null : e.target.value })}>
                {WINDOWS.map((w) => <option key={w.h} value={w.h}>{w.label}</option>)}</select></div>
            <div><Label htmlFor="a-outcome" className="mb-1.5 text-[13px] font-bold">Outcome</Label>
              <select id="a-outcome" className={SELECT} value={outcome} onChange={(e) => set({ outcome: e.target.value || null })}>
                <option value="">Any</option><option value="ok">Succeeded</option><option value="issues">Refused or failed</option>
                {d?.outcomes.filter((o) => o !== 'ok').map((o) => <option key={o} value={o}>{o}</option>)}</select></div>
            <div><Label htmlFor="a-action" className="mb-1.5 text-[13px] font-bold">Action</Label>
              <select id="a-action" className={SELECT} value={action} onChange={(e) => set({ action: e.target.value || null })}>
                <option value="">Any</option>{d?.actions.map((a) => <option key={a} value={a}>{a}</option>)}</select></div>
            <div><Label htmlFor="a-user" className="mb-1.5 text-[13px] font-bold">Staff ID</Label>
              <Input id="a-user" value={user} onChange={(e) => setUser(e.target.value)} maxLength={40} autoComplete="off" spellCheck={false} placeholder="e.g. DEMO-A1" className="h-10 rounded-sm" /></div>
            <div><Label htmlFor="a-text" className="mb-1.5 text-[13px] font-bold">Anything else</Label>
              <Input id="a-text" value={text} onChange={(e) => setText(e.target.value)} maxLength={40} autoComplete="off" spellCheck={false} placeholder="Customer, case or request ID" className="h-10 rounded-sm" /></div>
          </form>
        </Section>

        {q.isLoading && <Loading rows={6} />}
        {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
        {d && (
          <>
            <Figures items={[
              { label: 'Events in this period', value: int(d.total) },
              { label: 'Refused or failed', value: int(d.issues), tone: d.issues ? 'warn' : 'ok', note: d.issues ? 'Select “Refused or failed” to see them' : 'Nothing unusual' },
              { label: 'Showing', value: int(d.events.length), note: d.events.length < d.total ? 'Newest first; narrow the filters to see older ones' : 'All matching events' },
            ]} />
            <Section title="Events" flush>
              <DataTable
                data={d.events as unknown as Record<string, unknown>[]}
                caption="Access log"
                sortable
                empty="No events match these filters."
                columns={[
                  { key: 'at', label: 'When', render: (r) => <span className="whitespace-nowrap">{dateTime(r.at)}</span> },
                  { key: 'user_id', label: 'Staff ID', render: (r) => String(r.user_id ?? '–') },
                  { key: 'role', label: 'Role', render: (r) => (r.role ? ROLE[String(r.role)] ?? String(r.role) : '–') },
                  { key: 'action', label: 'Action' },
                  { key: 'object_id', label: 'Object', render: (r) => (r.object_id ? <span className="font-mono text-[13px]">{String(r.object_id)}</span> : '–') },
                  { key: 'outcome', label: 'Outcome', render: (r) => <StatusBadge tone={r.outcome === 'ok' ? 'ok' : 'danger'}>{r.outcome === 'ok' ? 'Succeeded' : String(r.outcome)}</StatusBadge> },
                  { key: 'request_id', label: 'Request', render: (r) => <span className="font-mono text-xs text-faint">{String(r.request_id ?? '')}</span> },
                ]}
              />
            </Section>
          </>
        )}
      </div>
    </>
  )
}
