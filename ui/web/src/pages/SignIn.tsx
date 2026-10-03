import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, FileLock2, KeyRound, Landmark, Search, ShieldCheck, UserCheck } from 'lucide-react'
import { useDeferredValue, useState } from 'react'
import { api, ApiError, qs } from '@/api/client'
import type { Options, StaffRow } from '@/api/types'
import { useAuth } from '@/auth'
import { DataTable, Loading, StatusBadge } from '@/components/app'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { isoDate } from '@/lib/format'
import { ROLE_LABEL, label } from '@/lib/labels'

const ASSURANCES = [
  { icon: KeyRound, title: 'Encrypted, short-lived sessions', text: 'Locks after 15 minutes of inactivity.' },
  { icon: UserCheck, title: 'Role-based access', text: 'You see only the cases and data your role allows.' },
  { icon: Eye, title: 'Customer data masked', text: 'Personal information is hidden unless your role needs it.' },
  { icon: FileLock2, title: 'Every action audited', text: 'Sign-ins, case views and decisions are logged.' },
]

export function SignIn() {
  const { notice } = useAuth()
  const qc = useQueryClient()
  const options = useQuery({ queryKey: ['options'], queryFn: () => api.get<Options>('/api/session/options') })
  const [search, setSearch] = useState('')
  const q = useDeferredValue(search)
  const [picked, setPicked] = useState<string | null>(null)
  const staff = useQuery({
    queryKey: ['staff', q],
    queryFn: () => api.get<{ staff: StaffRow[] }>(`/api/session/staff${qs({ q })}`),
    enabled: !!options.data?.demo_login,
  })
  const login = useMutation({
    mutationFn: (agent_id: string) => api.post('/api/session/login', { agent_id }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['me'] }),
  })
  const chosen = staff.data?.staff.find((s) => s.agent_id === picked)

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(380px,5fr)_7fr]">
      {/* Brand and assurance panel */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-sidebar px-10 py-10 lg:flex">
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-md border border-gold/40 bg-gold/10 text-gold">
            <Landmark className="size-5" />
          </span>
          <div className="leading-tight">
            <p className="text-lg font-semibold tracking-tight text-white">TrustIssues</p>
            <p className="text-[11px] tracking-[0.08em] text-sidebar-foreground/70 uppercase">Collections platform</p>
          </div>
        </div>
        <div className="max-w-md">
          <h1 className="text-3xl leading-tight font-semibold text-white">Collections decisioning for bank staff</h1>
          <p className="mt-3 text-sm text-sidebar-foreground/80">
            Next-best-action recommendations, approvals, customer identity review and certified reporting in one controlled
            workspace.
          </p>
          <ul className="mt-8 space-y-4">
            {ASSURANCES.map((a) => (
              <li key={a.title} className="flex gap-3">
                <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-primary/15 text-info">
                  <a.icon className="size-4" />
                </span>
                <div>
                  <p className="text-sm font-medium text-white">{a.title}</p>
                  <p className="text-sm text-sidebar-foreground/70">{a.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-sidebar-foreground/50">
          <ShieldCheck className="mr-1 inline size-3.5 align-[-2px]" />
          Authorised personnel only. Unauthorised access is prohibited and monitored.
        </p>
      </aside>

      {/* Sign-in */}
      <main className="flex items-center justify-center px-4 py-10 sm:px-8">
        <Card className="w-full max-w-2xl gap-0 py-0">
          <CardHeader className="border-b border-border py-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <CardTitle className="text-lg">Staff sign-in</CardTitle>
                <CardDescription>
                  {options.data ? `Business date ${isoDate(options.data.as_of_date)}` : 'Collections platform'}
                </CardDescription>
              </div>
              {options.data?.demo_login && <StatusBadge tone="warn">DEMO ENVIRONMENT</StatusBadge>}
            </div>
          </CardHeader>

          <CardContent className="space-y-4 py-5">
            {notice && (
              <Alert className="border-info/40">
                <AlertDescription className="text-info">{notice}</AlertDescription>
              </Alert>
            )}
            {options.isLoading && <Loading rows={3} />}
            {options.data && !options.data.demo_login && (
              <p className="text-sm text-muted-foreground">
                Sign-in is not configured on this server. In the bank this page hands over to single sign-on with
                multi-factor authentication.
              </p>
            )}
            {options.data?.demo_login && (
              <form
                id="signin"
                className="space-y-4"
                onSubmit={(e) => {
                  e.preventDefault()
                  if (picked) login.mutate(picked)
                }}
              >
                <p className="text-sm text-muted-foreground">
                  Demo sign-in: choose the member of staff to act as. Production uses the bank's single sign-on with MFA.
                </p>
                <div>
                  <Label htmlFor="staff-search" className="mb-1.5">
                    Find staff by name, staff ID, role, team or site
                  </Label>
                  <div className="relative">
                    <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-faint" />
                    <Input
                      id="staff-search"
                      className="h-9 pl-8"
                      placeholder="e.g. AG-1304, team lead, Halifax"
                      autoComplete="off"
                      maxLength={40}
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                  </div>
                </div>
                <div className="max-h-80 overflow-y-auto rounded-lg">
                  {staff.isLoading ? (
                    <Loading rows={4} />
                  ) : (
                    <DataTable
                      data={(staff.data?.staff ?? []) as unknown as Record<string, unknown>[]}
                      rowKey="agent_id"
                      selectedKey={picked}
                      empty="No staff match that search."
                      caption="Staff"
                      onRowClick={(r) => setPicked(String(r.agent_id))}
                      columns={[
                        {
                          key: 'pick',
                          label: '',
                          className: 'w-8',
                          render: (r) => (
                            <span
                              aria-hidden
                              className={`block size-3.5 rounded-full border ${picked === r.agent_id ? 'border-primary bg-primary ring-2 ring-primary/30' : 'border-input'}`}
                            />
                          ),
                        },
                        { key: 'name', label: 'Name', render: (r) => <span className="font-medium">{String(r.name ?? '—')}</span> },
                        { key: 'agent_id', label: 'Staff ID', render: (r) => <span className="font-mono text-xs">{String(r.agent_id)}</span> },
                        { key: 'role', label: 'Role', render: (r) => label(ROLE_LABEL, r.role as string) },
                        { key: 'team_id', label: 'Team' },
                        { key: 'site', label: 'Site' },
                      ]}
                    />
                  )}
                </div>
                {login.error && (
                  <Alert variant="destructive">
                    <AlertDescription>{login.error instanceof ApiError ? login.error.message : 'Sign-in failed.'}</AlertDescription>
                  </Alert>
                )}
              </form>
            )}
          </CardContent>

          {options.data?.demo_login && (
            <CardFooter className="flex items-center justify-between gap-4 border-t border-border py-4">
              <p className="text-sm text-muted-foreground">
                {chosen ? (
                  <>
                    Signing in as <span className="font-medium text-foreground">{chosen.name}</span>{' '}
                    <span className="font-mono text-xs">({chosen.agent_id})</span> · {label(ROLE_LABEL, chosen.role)}
                  </>
                ) : (
                  'Select a member of staff.'
                )}
              </p>
              <Button type="submit" form="signin" size="lg" className="px-6" disabled={!picked || login.isPending}>
                {login.isPending ? 'Signing in…' : 'Sign in'}
              </Button>
            </CardFooter>
          )}
        </Card>
      </main>
    </div>
  )
}
