import { ChevronDown } from 'lucide-react'
import { Suspense, useState, type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import type { Me } from '@/api/types'
import { useAuth, useIdleLock, useMe } from '@/auth'
import { Loading } from '@/components/app'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { dateTime, isoDate } from '@/lib/format'
import { EMPLOYMENT_LABEL, ROLE_DUTY, ROLE_LABEL, label, languages } from '@/lib/labels'
import { cn } from '@/lib/utils'

function navFor(me: Me) {
  return [
    { to: '/queue', label: 'My work queue', show: me.can.my_queue },
    { to: '/approvals', label: 'Team & approvals', show: me.can.approvals || me.role === 'qa_analyst' },
    { to: '/ask', label: 'Ask', show: me.can.ask },
    { to: '/identity', label: 'Identity review', show: me.can.identity_review },
    { to: '/status', label: 'Data quality', show: me.can.status },
  ].filter((n) => n.show)
}

function accessList(me: Me): string[] {
  const a: string[] = []
  if (me.can.my_queue) a.push('Own case queue')
  if (me.can.approvals) a.push('Approvals')
  if (me.role === 'qa_analyst') a.push('Decision review')
  if (me.can.identity_review) a.push('Identity review')
  a.push('Ask', 'Data quality')
  return a
}

export function Wordmark({ small }: { small?: boolean }) {
  return (
    <span className="flex items-baseline gap-3">
      <span className={cn('font-bold tracking-tight text-brand', small ? 'text-[20px]' : 'text-[24px]')}>TrustIssues</span>
      <span className="border-l border-input pl-3 text-[14px] text-muted-foreground">Collections</span>
    </span>
  )
}

export function Shell() {
  const me = useMe()
  const { signOut } = useAuth()
  const { warning, stay } = useIdleLock(me.idle_minutes)
  const [profileOpen, setProfileOpen] = useState(false)

  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>

      <header className="border-t-4 border-brand bg-white">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-4">
            <Wordmark />
            {me.demo && (
              <span className="border border-warn px-1.5 py-px text-[11px] font-bold text-warn" title="Synthetic data and local accounts">
                DEMO ENVIRONMENT
              </span>
            )}
          </div>
          <div className="flex items-center gap-5 text-[13px]">
            <span className="hidden text-muted-foreground md:inline">
              Business date <span className="text-foreground">{isoDate(me.as_of_date)}</span>
            </span>
            <DropdownMenu>
              <DropdownMenuTrigger className="flex items-center gap-2 border border-transparent px-2 py-1 text-left hover:border-border focus-visible:border-border">
                <span className="leading-tight">
                  <span className="block text-[14px] text-foreground">{me.name ?? me.agent_id}</span>
                  <span className="block text-xs text-muted-foreground">
                    {me.agent_id} · {label(ROLE_LABEL, me.role)}
                  </span>
                </span>
                <ChevronDown className="size-4 text-muted-foreground" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-60 rounded-sm">
                <DropdownMenuLabel className="font-normal">
                  <p className="text-sm text-foreground">{me.name ?? me.agent_id}</p>
                  <p className="text-xs text-muted-foreground">
                    Staff ID {me.agent_id} · {me.team_id ?? 'no team'}
                  </p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setProfileOpen(true)}>My profile and access</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void signOut()} className="text-brand focus:text-brand">
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        <nav aria-label="Main" className="bg-charcoal">
          <ul className="mx-auto flex max-w-[1400px] flex-wrap px-6">
            {navFor(me).map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  className={({ isActive }) =>
                    cn(
                      'block border-b-4 px-4 pt-3 pb-2 text-[14px] text-white no-underline hover:text-white',
                      isActive ? 'border-brand bg-[#2b2e31]' : 'border-transparent hover:border-[#8a8d90]',
                    )
                  }
                >
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-6 py-6">
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="no-print bg-charcoal text-[12px] text-[#d0d1d2]">
        <div className="mx-auto flex max-w-[1400px] flex-wrap justify-between gap-2 px-6 py-3">
          <span>For internal use by authorized staff only. Activity on this system is logged.</span>
          <span>
            You are signed in as {me.agent_id}. Your session ends after {me.idle_minutes} minutes without activity.
          </span>
        </div>
      </footer>
      <p className="print-footer hidden px-6 py-2 text-xs">
        Internal — contains customer information. Printed by {me.agent_id} on {new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC.
      </p>

      <ProfileSheet me={me} open={profileOpen} onOpenChange={setProfileOpen} />

      <AlertDialog open={warning}>
        <AlertDialogContent className="rounded-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-normal">Your session is about to end</AlertDialogTitle>
            <AlertDialogDescription>For your security you will be signed out in about two minutes because there has been no activity.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => void signOut()}>Sign out</AlertDialogCancel>
            <AlertDialogAction autoFocus onClick={() => void stay()}>
              Stay signed in
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/** Your own record only. Nobody else's details are ever sent to the browser. */
function ProfileSheet({ me, open, onOpenChange }: { me: Me; open: boolean; onOpenChange: (o: boolean) => void }) {
  const record: [string, ReactNode][] = [
    ['Staff ID', me.agent_id],
    ['Name', me.name ?? '—'],
    ['Role', label(ROLE_LABEL, me.role)],
    ['Team', me.team_id ?? '—'],
    ['Reports to', me.team_lead_id ?? '—'],
    ['Site', me.site ?? '—'],
    ['Employment', label(EMPLOYMENT_LABEL, me.employment_type)],
    ['Languages', languages(me.languages)],
    ['Vulnerable customers', me.certified ? 'Certified' : 'Not certified'],
  ]
  const session: [string, ReactNode][] = [
    ['Signed in', dateTime(me.signed_in_at)],
    ['Ends after', `${me.idle_minutes} minutes without activity, or ${me.max_hours} hours`],
  ]
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto border-l-4 border-l-brand sm:max-w-md">
        <SheetHeader className="border-b border-border">
          <SheetTitle className="text-[22px] font-normal">{me.name ?? me.agent_id}</SheetTitle>
          <SheetDescription>
            {label(ROLE_LABEL, me.role)} · Staff ID {me.agent_id}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-6 px-4 pb-6">
          <p className="text-sm text-muted-foreground">{ROLE_DUTY[me.role] ?? ''}</p>
          <ProfileTable title="Staff record" rows={record} />
          <div>
            <h3 className="mb-2 text-[15px] font-bold">Access</h3>
            <ul className="list-disc space-y-0.5 pl-5 text-sm">
              {accessList(me).map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted-foreground">Customer information is masked for your role before it reaches this screen.</p>
          </div>
          <ProfileTable title="Session" rows={session} />
        </div>
      </SheetContent>
    </Sheet>
  )
}

function ProfileTable({ title, rows }: { title: string; rows: [string, ReactNode][] }) {
  return (
    <div>
      <h3 className="mb-2 text-[15px] font-bold">{title}</h3>
      <table className="w-full border border-border text-sm">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} className="border-b border-[#e6e6e6] last:border-b-0">
              <th scope="row" className="w-40 bg-panel px-3 py-2 text-left font-normal text-muted-foreground">
                {k}
              </th>
              <td className="px-3 py-2">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
