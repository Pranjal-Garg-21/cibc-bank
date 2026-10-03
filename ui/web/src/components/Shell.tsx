import {
  BadgeCheck,
  CalendarDays,
  ChevronsUpDown,
  Database,
  Fingerprint,
  IdCard,
  Landmark,
  ListChecks,
  LockKeyhole,
  LogOut,
  MessageSquareText,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { Suspense, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import type { Me } from '@/api/types'
import { useAuth, useIdleLock, useMe } from '@/auth'
import { Loading, StatusBadge } from '@/components/app'
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
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
} from '@/components/ui/sidebar'
import { dateTime, isoDate } from '@/lib/format'
import { EMPLOYMENT_LABEL, ROLE_DUTY, ROLE_LABEL, initials, label, languages } from '@/lib/labels'

type NavItem = { to: string; label: string; icon: typeof ListChecks; show: boolean; connected: boolean }

function navFor(me: Me): { group: string; items: NavItem[] }[] {
  return [
    {
      group: 'Collections operations',
      items: [
        { to: '/queue', label: 'My work queue', icon: ListChecks, show: me.can.my_queue, connected: me.features.queue },
        {
          to: '/approvals',
          label: 'Team & approvals',
          icon: Users,
          show: me.can.approvals || me.role === 'qa_analyst',
          connected: me.features.queue,
        },
        { to: '/ask', label: 'Ask', icon: MessageSquareText, show: me.can.ask, connected: me.features.ask },
      ],
    },
    {
      group: 'Data governance',
      items: [
        { to: '/identity', label: 'Identity review', icon: Fingerprint, show: me.can.identity_review, connected: me.features.identity },
        { to: '/status', label: 'Data quality & status', icon: Database, show: me.can.status, connected: me.features.status },
      ],
    },
  ]
}

/** Plain-language list of what this person may do, for the operator bar and profile. */
function accessList(me: Me): string[] {
  const a: string[] = []
  if (me.can.my_queue) a.push('Own case queue')
  if (me.can.approvals) a.push('Approvals')
  if (me.role === 'qa_analyst') a.push('Decision review')
  if (me.can.identity_review) a.push('Identity review')
  a.push('Ask', 'Data status')
  return a
}

const PAGE_TITLE: Record<string, string> = {
  queue: 'My work queue',
  approvals: 'Team & approvals',
  ask: 'Ask',
  cases: 'Decision card',
  identity: 'Identity review',
  status: 'Data quality & status',
}

export function Shell() {
  const me = useMe()
  const { signOut } = useAuth()
  const { warning, stay, minutesLeft } = useIdleLock(me.idle_minutes)
  const [profileOpen, setProfileOpen] = useState(false)
  const loc = useLocation()
  const section = PAGE_TITLE[loc.pathname.split('/')[1] ?? ''] ?? ''

  return (
    <SidebarProvider>
      <Sidebar collapsible="none" className="sticky top-0 h-svh border-r border-sidebar-border">
        <SidebarHeader className="border-b border-sidebar-border px-4 py-4">
          <div className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-md border border-gold/40 bg-gold/10 text-gold">
              <Landmark className="size-5" />
            </span>
            <div className="leading-tight">
              <p className="text-[15px] font-semibold tracking-tight text-white">TrustIssues</p>
              <p className="text-[11px] tracking-[0.06em] text-sidebar-foreground/70 uppercase">Collections platform</p>
            </div>
          </div>
        </SidebarHeader>

        <SidebarContent className="px-1 py-2">
          {navFor(me).map((g) => {
            const items = g.items.filter((i) => i.show)
            if (!items.length) return null
            return (
              <SidebarGroup key={g.group}>
                <SidebarGroupLabel className="text-[10.5px] tracking-[0.08em] text-sidebar-foreground/55 uppercase">{g.group}</SidebarGroupLabel>
                <SidebarGroupContent>
                  <SidebarMenu>
                    {items.map((n) => (
                      <SidebarMenuItem key={n.to}>
                        <SidebarMenuButton asChild isActive={loc.pathname.startsWith(n.to)} className="h-9 data-[active=true]:shadow-[inset_3px_0_0_var(--gold)]">
                          <NavLink to={n.to}>
                            <n.icon />
                            <span>{n.label}</span>
                          </NavLink>
                        </SidebarMenuButton>
                        {!n.connected && <SidebarMenuBadge className="text-[9.5px] tracking-wide text-faint uppercase">Pending</SidebarMenuBadge>}
                      </SidebarMenuItem>
                    ))}
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            )
          })}
        </SidebarContent>

        <SidebarFooter className="border-t border-sidebar-border p-2">
          <div className="px-2 pb-1 text-[10.5px] leading-snug text-sidebar-foreground/50">
            <ShieldCheck className="mr-1 inline size-3 align-[-2px]" />
            Internal system. All access is logged and monitored.
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton size="lg" className="h-auto py-2">
                <Avatar className="size-8 rounded-md">
                  <AvatarFallback className="rounded-md bg-primary/25 text-xs font-semibold text-info">
                    {initials(me.name, me.agent_id)}
                  </AvatarFallback>
                </Avatar>
                <div className="grid flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-medium text-white">{me.name ?? me.agent_id}</span>
                  <span className="truncate font-mono text-[11px] text-sidebar-foreground/70">{me.agent_id}</span>
                  <span className="truncate text-[11px] text-sidebar-foreground/70">{label(ROLE_LABEL, me.role)}</span>
                </div>
                <ChevronsUpDown className="ml-auto size-4 opacity-60" />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="start" className="w-64">
              <DropdownMenuLabel className="font-normal">
                <p className="text-sm font-medium">{me.name ?? me.agent_id}</p>
                <p className="font-mono text-xs text-muted-foreground">Staff ID {me.agent_id}</p>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuItem onSelect={() => setProfileOpen(true)}>
                  <IdCard /> Staff profile & access
                </DropdownMenuItem>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => void signOut()}>
                <LogOut /> Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset className="min-w-0 bg-background">
        <header className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur">
          <div className="flex min-h-12 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-6 py-2">
            <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm whitespace-nowrap">
              <span className="text-muted-foreground">Collections</span>
              <span className="text-faint">/</span>
              <span className="font-medium text-foreground">{section}</span>
            </nav>
            <div className="flex flex-wrap items-center gap-2">
              {me.demo && (
                <StatusBadge tone="warn" title="Demo sign-in is enabled; production uses the bank's single sign-on with MFA">
                  DEMO ENVIRONMENT
                </StatusBadge>
              )}
              <StatusBadge title="All figures are as of this business date">
                <CalendarDays className="size-3" /> Business date {isoDate(me.as_of_date)}
              </StatusBadge>
              <StatusBadge tone={minutesLeft <= 2 ? 'warn' : 'ok'} title="Session locks after inactivity">
                <LockKeyhole className="size-3" /> Secure session · {minutesLeft} min
              </StatusBadge>
            </div>
          </div>
          <OperatorBar me={me} onProfile={() => setProfileOpen(true)} />
        </header>

        <div id="main" className="mx-auto w-full max-w-[1440px] flex-1 px-6 py-6">
          {/* pages load on first use; the shell stays on screen meanwhile */}
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </div>
        <p className="print-footer hidden px-6 py-2 text-xs">
          Internal — contains customer information. Printed by {me.agent_id} on {new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC.
        </p>
      </SidebarInset>

      <ProfileSheet me={me} open={profileOpen} onOpenChange={setProfileOpen} />

      <AlertDialog open={warning}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Your session is about to lock</AlertDialogTitle>
            <AlertDialogDescription>
              For security you will be signed out in about two minutes because there has been no activity.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => void signOut()}>Sign out now</AlertDialogCancel>
            <AlertDialogAction autoFocus onClick={() => void stay()}>
              Stay signed in
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SidebarProvider>
  )
}

/** The signed-in operator, always visible: who, which role, which team and site, and what they may do. */
function OperatorBar({ me, onProfile }: { me: Me; onProfile: () => void }) {
  const items: [string, string][] = [
    ['Staff ID', me.agent_id],
    ['Role', label(ROLE_LABEL, me.role)],
    ['Team', me.team_id ?? '—'],
    ['Site', me.site ?? '—'],
  ]
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-border/70 bg-card/60 px-6 py-2 text-xs">
      <span className="font-medium text-foreground">{me.name ?? me.agent_id}</span>
      {items.map(([k, v]) => (
        <span key={k} className="text-muted-foreground">
          {k} <span className={k === 'Staff ID' ? 'font-mono text-foreground' : 'text-foreground'}>{v}</span>
        </span>
      ))}
      {me.certified && (
        <StatusBadge tone="ok" title="Certified to handle vulnerable customers">
          <BadgeCheck className="size-3" /> Vulnerable-customer certified
        </StatusBadge>
      )}
      <button type="button" onClick={onProfile} className="ml-auto text-info hover:underline">
        View profile & access
      </button>
    </div>
  )
}

function ProfileSheet({ me, open, onOpenChange }: { me: Me; open: boolean; onOpenChange: (o: boolean) => void }) {
  const rows: [string, React.ReactNode][] = [
    ['Staff ID', <span className="font-mono">{me.agent_id}</span>],
    ['Name', me.name ?? '—'],
    ['Role', label(ROLE_LABEL, me.role)],
    ['Team', me.team_id ?? '—'],
    ['Reports to', me.team_lead_id ? <span className="font-mono">{me.team_lead_id}</span> : '—'],
    ['Site', me.site ?? '—'],
    ['Employment', label(EMPLOYMENT_LABEL, me.employment_type)],
    ['Languages', languages(me.languages)],
    ['Vulnerable customers', me.certified ? 'Certified' : 'Not certified'],
  ]
  const session: [string, React.ReactNode][] = [
    ['Signed in', dateTime(me.signed_in_at)],
    ['Locks after', `${me.idle_minutes} minutes without activity`],
    ['Maximum session', `${me.max_hours} hours`],
    ['Sign-in method', me.demo ? 'Demo staff picker (no MFA)' : 'Single sign-on'],
  ]
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <div className="flex items-center gap-3">
            <Avatar className="size-12 rounded-lg">
              <AvatarFallback className="rounded-lg bg-primary/25 text-base font-semibold text-info">{initials(me.name, me.agent_id)}</AvatarFallback>
            </Avatar>
            <div>
              <SheetTitle>{me.name ?? me.agent_id}</SheetTitle>
              <SheetDescription>
                {label(ROLE_LABEL, me.role)} · <span className="font-mono">{me.agent_id}</span>
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>
        <div className="space-y-6 px-4 pb-6">
          <p className="rounded-md border border-border bg-secondary/40 px-3 py-2 text-sm text-muted-foreground">{ROLE_DUTY[me.role] ?? ''}</p>
          <ProfileList title="Staff record" rows={rows} />
          <div>
            <h3 className="mb-2 text-xs font-semibold tracking-[0.06em] text-muted-foreground uppercase">Access granted</h3>
            <div className="flex flex-wrap gap-1.5">
              {accessList(me).map((a) => (
                <Badge key={a} variant="outline" className="rounded-md">
                  {a}
                </Badge>
              ))}
            </div>
            <p className="mt-2 text-xs text-faint">Customer data is masked for your role before it reaches this screen.</p>
          </div>
          <Separator />
          <ProfileList title="Session" rows={session} />
        </div>
      </SheetContent>
    </Sheet>
  )
}

function ProfileList({ title, rows }: { title: string; rows: [string, React.ReactNode][] }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold tracking-[0.06em] text-muted-foreground uppercase">{title}</h3>
      <dl className="divide-y divide-border rounded-md border border-border text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[10rem_1fr] gap-3 px-3 py-2">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="text-foreground">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
