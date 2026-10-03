import { NavLink, Outlet } from 'react-router-dom'
import { useAuth, useIdleLock, useMe } from '../auth'
import { isoDate } from '../lib/format'
import { ROLE_LABEL, label } from '../lib/labels'
import { Badge, Button, Dialog, cx } from './ui'

type NavItem = { to: string; label: string; show: boolean; connected: boolean }

export function Shell() {
  const me = useMe()
  const { signOut } = useAuth()
  const { warning, stay } = useIdleLock(me.idle_minutes)
  const nav: NavItem[] = [
    { to: '/ask', label: 'Ask', show: me.can.ask, connected: me.features.ask },
    { to: '/queue', label: 'My queue', show: me.can.my_queue, connected: me.features.queue },
    { to: '/approvals', label: 'Team & approvals', show: me.can.approvals || me.role === 'qa_analyst', connected: me.features.queue },
    { to: '/identity', label: 'Identity review', show: me.can.identity_review, connected: me.features.identity },
    { to: '/status', label: 'Data status', show: me.can.status, connected: me.features.status },
  ]
  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-raised focus:px-3 focus:py-2">
        Skip to content
      </a>
      <nav aria-label="Main" className="fixed inset-y-0 left-0 flex w-60 flex-col border-r border-border bg-surface">
        <div className="border-b border-border px-5 py-4">
          <p className="text-[15px] font-semibold tracking-tight text-text">TrustIssues</p>
          <p className="text-xs text-muted">Collections decisioning</p>
        </div>
        <ul className="flex-1 space-y-0.5 p-3">
          {nav
            .filter((n) => n.show)
            .map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  className={({ isActive }) =>
                    cx(
                      'flex items-center justify-between rounded-md px-3 py-2 text-sm',
                      isActive ? 'bg-raised font-medium text-text shadow-[inset_2px_0_0_var(--color-accent-text)]' : 'text-muted hover:bg-raised hover:text-text',
                    )
                  }
                >
                  {n.label}
                  {!n.connected && <span className="text-[10px] tracking-wide text-faint uppercase">Soon</span>}
                </NavLink>
              </li>
            ))}
        </ul>
        <div className="border-t border-border p-4 text-xs text-faint">
          Internal use only. Activity on this system is logged.
        </div>
      </nav>

      <div className="ml-60 flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-4 border-b border-border bg-bg/95 px-6 backdrop-blur">
          <div className="flex items-center gap-3">
            <Badge tone="neutral" title="All figures are as of this date">
              Data as of {isoDate(me.as_of_date)}
            </Badge>
            {me.demo && (
              <Badge tone="warn" title="Demo sign-in is enabled; there is no real authentication">
                DEMO
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-4">
            <div className="text-right leading-tight">
              <p className="text-sm text-text">{me.name ?? me.agent_id}</p>
              <p className="text-xs text-muted">
                {label(ROLE_LABEL, me.role)} · {me.agent_id}
                {me.team_id ? ` · Team ${me.team_id}` : ''}
              </p>
            </div>
            <Button variant="ghost" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1440px] flex-1 px-6 py-6">
          <Outlet />
        </main>
        <p className="print-footer hidden px-6 py-2 text-xs">
          Internal — contains customer information. Printed by {me.agent_id} on {new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC.
        </p>
      </div>

      <Dialog open={warning} onClose={() => void stay()} title="Still there?">
        <p>For security you will be signed out in about two minutes because there has been no activity.</p>
        <div className="mt-5 flex justify-end gap-3">
          <Button variant="ghost" onClick={() => void signOut()}>
            Sign out now
          </Button>
          <Button variant="primary" autoFocus onClick={() => void stay()}>
            Stay signed in
          </Button>
        </div>
      </Dialog>
    </div>
  )
}
