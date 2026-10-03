import { ChevronDown, LogOut, ShieldCheck, Clock3 } from 'lucide-react'
import { Suspense, useState, type ReactNode } from 'react'
import { Tabs } from 'radix-ui'
import { NavLink, Outlet } from 'react-router-dom'
import type { Me } from '@/api/types'
import { useAuth, useIdleLock, useMe } from '@/auth'
import { Loading } from '@/components/app'
import { SecurityPanel } from '@/components/SecurityPanel'
import { Wordmark } from '@/components/Wordmark'
import { Button } from '@/components/ui/button'
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
    { to: '/approvals', label: 'Team & approvals', show: me.can.approvals },
    { to: '/team', label: 'Agent accounts', show: me.can.manage_agents },
    { to: '/ask', label: 'Ask', show: me.can.ask },
    { to: '/identity', label: 'Identity review', show: me.can.identity_review },
    { to: '/status', label: 'Data quality', show: me.can.status },
  ].filter((n) => n.show)
}

function accessList(me: Me): string[] {
  const a: string[] = []
  if (me.can.my_queue) a.push('Own case queue')
  if (me.can.approvals) a.push('Approvals')
  if (me.can.manage_agents) a.push("Agent accounts: set your agents' passwords")
  if (me.can.identity_review) a.push('Identity review')
  a.push('Ask', 'Data quality')
  return a
}

export function Shell() {
  const me = useMe()
  const { signOut } = useAuth()
  const { warning, stay, secondsLeft } = useIdleLock(me.idle_minutes, me.max_hours, me.signed_in_at)
  const [profileOpen, setProfileOpen] = useState(false)
  const [profileTab, setProfileTab] = useState<'profile' | 'security'>('profile')

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-40 focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>

      <header className="border-t-4 border-brand bg-white">
        <div className="console-width flex min-h-[72px] flex-wrap items-center justify-between gap-3 py-3">
          <div className="flex flex-wrap items-center gap-4">
            <Wordmark />
            {me.demo && (
              <span className="rounded-sm border border-warn px-2 py-1 text-[11px] font-bold text-warn" title="Synthetic data and local accounts">
                DEMO ENVIRONMENT
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 text-[13px] sm:gap-5">
            <span className="hidden text-muted-foreground md:inline">
              Business date <span className="text-foreground">{isoDate(me.as_of_date)}</span>
            </span>
            <DropdownMenu>
              <DropdownMenuTrigger className="flex min-h-11 items-center gap-2 rounded-sm border border-transparent px-2 py-1 text-left hover:border-border focus-visible:border-border" aria-label="My profile and access">
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
                <DropdownMenuItem onSelect={() => { setProfileTab('profile'); setProfileOpen(true) }}>My profile and access</DropdownMenuItem>
                <DropdownMenuItem onSelect={() => void signOut()} className="text-brand focus:text-brand">
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="ghost" size="sm" className="no-print min-h-11" onClick={() => void signOut()}><LogOut aria-hidden="true" strokeWidth={1.5} />Sign out</Button>
          </div>
        </div>
        <nav aria-label="Main" className="bg-charcoal">
          <ul className="console-width flex overflow-x-auto">
            {navFor(me).map((n) => (
              <li key={n.to}>
                <NavLink
                  to={n.to}
                  className={({ isActive }) =>
                    cn(
                      'block border-b-4 px-4 pt-3 pb-3 text-[14px] whitespace-nowrap text-white no-underline hover:text-white',
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

      <main id="main" tabIndex={-1} className="console-width flex-1 py-7 md:py-9">
        <div className="no-print mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4 text-[13px] text-muted-foreground">
          <button type="button" className="flex min-h-8 flex-wrap items-center gap-2 text-left hover:text-brand" onClick={() => { setProfileTab('security'); setProfileOpen(true) }}><ShieldCheck className="size-4" strokeWidth={1.5} aria-hidden="true" />{me.mfa ? 'Multi-factor authentication verified' : 'Demo access: MFA disabled'}<span className="ml-2 text-brand underline underline-offset-4">Session & security</span></button>
          <span className="flex items-center gap-2 tabular-nums"><Clock3 className="size-4" strokeWidth={1.5} aria-hidden="true" />{secondsLeft === null ? 'Automatic session expiry is off for this demo' : `Session expires in ${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, '0')}`}</span>
        </div>
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="no-print bg-charcoal text-[12px] text-[#d0d1d2]">
        <div className="console-width flex flex-wrap justify-between gap-2 py-4">
          <span>For internal use by authorized staff only. Activity on this system is logged.</span>
          <span>
            You are signed in as {me.agent_id}. {me.idle_minutes === null && me.max_hours === null ? 'Sign out when you finish.' : me.idle_minutes !== null ? `Your session ends after ${me.idle_minutes} minutes without activity.` : `Your session ends after ${me.max_hours} hours.`}
          </span>
        </div>
      </footer>
      <p className="print-footer hidden px-6 py-2 text-xs">
        Internal: contains customer information. Printed by {me.agent_id} on {new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC.
      </p>

      <ProfileSheet me={me} open={profileOpen} onOpenChange={setProfileOpen} initialTab={profileTab} />

      <AlertDialog open={warning}>
        <AlertDialogContent className="rounded-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-normal">Your session is about to end</AlertDialogTitle>
            <AlertDialogDescription>Your session ends in {secondsLeft} seconds. Stay signed in to continue, or sign out. The absolute session limit still applies.</AlertDialogDescription>
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
function ProfileSheet({ me, open, onOpenChange, initialTab }: { me: Me; open: boolean; onOpenChange: (o: boolean) => void; initialTab: 'profile' | 'security' }) {
  const record: [string, ReactNode][] = [
    ['Staff ID', me.agent_id],
    ['Name', me.name ?? '-'],
    ['Role', label(ROLE_LABEL, me.role)],
    ['Team', me.team_id ?? '-'],
    ['Reports to', me.team_lead_id ?? '-'],
    ['Site', me.site ?? '-'],
    ['Employment', label(EMPLOYMENT_LABEL, me.employment_type)],
    ['Languages', languages(me.languages)],
    ['Vulnerable customers', me.certified ? 'Certified' : 'Not certified'],
  ]
  const session: [string, ReactNode][] = [
    ['Signed in', dateTime(me.signed_in_at)],
    ['Ends after', me.idle_minutes === null && me.max_hours === null ? 'Sign-out or server restart (demo)' :
      [me.idle_minutes !== null ? `${me.idle_minutes} minutes without activity` : null,
        me.max_hours !== null ? `${me.max_hours} hours` : null].filter(Boolean).join(', or ')],
  ]
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto border-l-4 border-l-brand sm:max-w-lg">
        <SheetHeader className="border-b border-border">
          <SheetTitle className="text-[22px] font-normal">{me.name ?? me.agent_id}</SheetTitle>
          <SheetDescription>
            {label(ROLE_LABEL, me.role)} · Staff ID {me.agent_id}
          </SheetDescription>
        </SheetHeader>
        <Tabs.Root key={open ? initialTab : 'closed'} defaultValue={initialTab} className="px-5 pb-6">
          <Tabs.List aria-label="Account details" className="mb-6 grid grid-cols-2 border-b border-border">
            <Tabs.Trigger value="profile" className="min-h-11 border-b-2 border-transparent px-3 text-sm text-muted-foreground data-[state=active]:border-brand data-[state=active]:font-bold data-[state=active]:text-brand">Profile & access</Tabs.Trigger>
            <Tabs.Trigger value="security" className="min-h-11 border-b-2 border-transparent px-3 text-sm text-muted-foreground data-[state=active]:border-brand data-[state=active]:font-bold data-[state=active]:text-brand">Session & security</Tabs.Trigger>
          </Tabs.List>
          <Tabs.Content value="profile" className="space-y-7">
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
          </Tabs.Content>
          <Tabs.Content value="security" className="space-y-6"><SecurityPanel me={me} /><ProfileTable title="Session limits" rows={session} /></Tabs.Content>
        </Tabs.Root>
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
