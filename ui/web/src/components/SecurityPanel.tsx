import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Monitor, ShieldCheck } from 'lucide-react'
import { api } from '@/api/client'
import type { Me, SecurityInfo } from '@/api/types'
import { ErrorState, Loading, Notice, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { dateTime } from '@/lib/format'

const ACTIONS: Record<string, string> = { sign_in: 'Sign-in', sign_out: 'Sign-out', reauthenticate: 'Identity confirmation', sign_out_others: 'Other sessions signed out' }
function browserName(agent: string) {
  if (agent.includes('Edg/')) return 'Microsoft Edge'
  if (agent.includes('Firefox/')) return 'Firefox'
  if (agent.includes('Chrome/')) return 'Chrome'
  if (agent.includes('Safari/')) return 'Safari'
  return 'Browser session'
}

export function SecurityPanel({ me }: { me: Me }) {
  const q = useQuery({ queryKey: ['session-security'], queryFn: () => api.get<SecurityInfo>('/api/session/security'), staleTime: 0 })
  const [confirming, setConfirming] = useState(false)
  const endOthers = useMutation({
    mutationFn: () => api.post<{ ended: number }>('/api/session/logout-others'),
    onSuccess: () => { setConfirming(false); void q.refetch() },
  })
  const others = q.data?.sessions.filter((s) => !s.current).length ?? 0
  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-3 flex items-center gap-2 text-[16px] font-bold"><ShieldCheck className="size-5 text-brand" strokeWidth={1.5} aria-hidden="true" />Account security</h3>
        <dl className="space-y-3 text-sm">
          <div className="flex items-center justify-between gap-3"><dt>Multi-factor authentication</dt><dd><StatusBadge tone={me.mfa ? 'ok' : 'warn'}>{me.mfa ? 'Verified' : 'Demo: disabled'}</StatusBadge></dd></div>
          <div className="flex items-center justify-between gap-3"><dt>Connection</dt><dd>{me.transport_secure ? 'HTTPS' : 'Local HTTP preview'}</dd></div>
        </dl>
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">Lost your authenticator? Use an unused recovery code or contact the service desk. Changing a second factor requires identity verification.</p>
      </div>
      {q.isLoading && <Loading rows={2} />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && <>
        <div>
          <h3 className="mb-3 text-[16px] font-bold">Active sessions</h3>
          <ul className="security-list">
            {q.data.sessions.map((s) => <li key={s.id} className="flex items-start gap-3">
              <Monitor className="mt-0.5 size-4 shrink-0 text-muted-foreground" strokeWidth={1.5} aria-hidden="true" />
              <div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3 text-sm"><span>{browserName(s.device)}</span>{s.current && <StatusBadge>This browser</StatusBadge>}</div>
                <p className="mt-1 text-xs text-muted-foreground">Signed in {dateTime(s.signed_in_at)}</p>
              </div>
            </li>)}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">Up to {q.data.max_sessions} active browsers. A new sign-in ends the oldest session when this limit is reached.</p>
          {others > 0 && <div className="mt-4">
            {confirming ? <div className="space-y-3 rounded-sm border border-border p-3">
              <p className="text-sm">Sign out {others} other {others === 1 ? 'browser' : 'browsers'}? This browser stays signed in.</p>
              <div className="flex flex-wrap gap-2"><Button variant="secondary" size="sm" disabled={endOthers.isPending} onClick={() => setConfirming(false)}>Cancel</Button>
                <Button size="sm" disabled={endOthers.isPending} onClick={() => endOthers.mutate()}>Confirm sign-out</Button></div>
            </div> : <Button variant="outline" onClick={() => setConfirming(true)}>Sign out other browsers</Button>}
          </div>}
          {endOthers.error && <div className="mt-3"><ErrorState error={endOthers.error} /></div>}
          {endOthers.isSuccess && <div className="mt-3"><Notice tone="ok">{endOthers.data.ended} other sessions ended.</Notice></div>}
        </div>
        <div>
          <h3 className="mb-3 text-[16px] font-bold">Recent security activity</h3>
          <ul className="security-list">
            {q.data.events.map((event, i) => <li key={event.at + i}>
              <div className="flex items-center justify-between gap-3 text-sm"><span>{ACTIONS[event.action] ?? 'Account activity'}</span><StatusBadge tone={event.outcome === 'ok' ? 'neutral' : 'warn'}>{event.outcome === 'ok' ? 'Completed' : 'Refused'}</StatusBadge></div>
              <p className="mt-1 text-xs text-muted-foreground">{dateTime(event.at)}</p>
            </li>)}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">If you do not recognize an event, sign out other browsers and contact the service desk.</p>
        </div>
      </>}
    </div>
  )
}
