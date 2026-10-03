import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '@/api/client'
import type { Options } from '@/api/types'
import { useAuth } from '@/auth'
import { Notice } from '@/components/app'
import { Wordmark } from '@/components/Shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

type Role = 'team_lead' | 'agent'
const ROLES: { id: Role; label: string; hint: string; help: string }[] = [
  {
    id: 'agent',
    label: 'Agent',
    hint: 'Use your staff ID and the password your team lead gave you.',
    help: 'Forgot your password or locked out? Ask your team lead to reset it.',
  },
  {
    id: 'team_lead',
    label: 'Team lead',
    hint: 'Use your staff ID and password.',
    help: 'Forgot your password or locked out? Contact the service desk.',
  },
]

/**
 * Staff sign-in for the two roles that use this system: agents and team leads. Nothing about any member of staff is
 * shown or fetched before sign-in. (In the bank this page would hand over to single sign-on.)
 */
export function SignIn() {
  const { notice } = useAuth()
  const qc = useQueryClient()
  const options = useQuery({ queryKey: ['options'], queryFn: () => api.get<Options>('/api/session/options') })
  const [role, setRole] = useState<Role>('agent')
  const [staffId, setStaffId] = useState('')
  const [password, setPassword] = useState('')
  const [capsLock, setCapsLock] = useState(false)
  const login = useMutation({
    mutationFn: () => api.post('/api/session/login', { role, staff_id: staffId.trim(), password }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['me'] }),
    onSettled: () => setPassword(''), // never keep the password in memory longer than needed
  })
  const o = options.data
  const current = ROLES.find((r) => r.id === role)!

  return (
    <div className="flex min-h-screen flex-col bg-panel">
      <header className="border-t-4 border-brand bg-white">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between px-6 py-3">
          <Wordmark />
          {o?.demo && <span className="border border-warn px-1.5 py-px text-[11px] font-bold text-warn">DEMO ENVIRONMENT</span>}
        </div>
        <div className="h-3 bg-charcoal" />
      </header>

      <main className="flex flex-1 items-start justify-center px-4 py-14">
        <div className="w-full max-w-[420px]">
          <h1 className="mb-5 text-[28px]">Sign in</h1>

          {notice && (
            <div className="mb-4">
              <Notice tone="info">{notice}</Notice>
            </div>
          )}

          {o && !o.sign_in_configured ? (
            <Notice tone="warn" title="Sign-in is not set up on this server">
              Ask your administrator to create your account.
            </Notice>
          ) : (
            <>
            <div role="tablist" aria-label="Sign in as" className="grid grid-cols-2 border border-b-0 border-border bg-white">
              {ROLES.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  role="tab"
                  aria-selected={role === r.id}
                  onClick={() => {
                    setRole(r.id)
                    login.reset()
                  }}
                  className={
                    role === r.id
                      ? 'border-t-4 border-brand bg-white px-4 pt-2.5 pb-3 text-[15px] font-bold text-foreground'
                      : 'border-t-4 border-transparent border-b border-b-border bg-panel px-4 pt-2.5 pb-3 text-[15px] text-muted-foreground hover:text-foreground'
                  }
                >
                  {r.label}
                </button>
              ))}
            </div>
            <form
              role="tabpanel"
              aria-label={`${current.label} sign-in`}
              className="border border-t-0 border-border bg-white p-6"
              onSubmit={(e) => {
                e.preventDefault()
                if (staffId.trim() && password) login.mutate()
              }}
            >
              <p className="mb-4 text-sm text-muted-foreground">{current.hint}</p>
              <div className="mb-4">
                <Label htmlFor="staff-id" className="mb-1.5 text-[14px] font-bold">
                  Staff ID
                </Label>
                <Input
                  id="staff-id"
                  name="username"
                  autoComplete="username"
                  autoCapitalize="characters"
                  spellCheck={false}
                  maxLength={40}
                  required
                  className="h-10 rounded-sm text-[15px]"
                  value={staffId}
                  onChange={(e) => setStaffId(e.target.value)}
                />
              </div>
              <div className="mb-5">
                <Label htmlFor="password" className="mb-1.5 text-[14px] font-bold">
                  Password
                </Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  maxLength={128}
                  required
                  className="h-10 rounded-sm text-[15px]"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyUp={(e) => setCapsLock(e.getModifierState('CapsLock'))}
                  aria-describedby={capsLock ? 'caps' : undefined}
                />
                {capsLock && (
                  <p id="caps" className="mt-1 text-xs text-warn">
                    Caps Lock is on.
                  </p>
                )}
              </div>
              {login.error && (
                <p role="alert" className="mb-4 border-l-4 border-danger bg-[#f5e3e8] px-3 py-2 text-sm text-danger">
                  {login.error instanceof ApiError ? login.error.message : 'Sign-in failed. Try again.'}
                </p>
              )}
              <Button type="submit" size="lg" className="w-full" disabled={login.isPending || !o}>
                {login.isPending ? 'Signing in…' : `Sign in as ${current.label.toLowerCase()}`}
              </Button>
              <p className="mt-4 text-[13px] text-muted-foreground">{current.help}</p>
            </form>
            </>
          )}
          <p className="mt-6 text-xs leading-relaxed text-muted-foreground">
            This system is for authorized staff only. All activity is monitored and logged. Accounts lock after repeated
            failed attempts.
          </p>
        </div>
      </main>

      <footer className="bg-charcoal px-6 py-3 text-center text-[12px] text-[#d0d1d2]">TrustIssues · Collections decisioning · Internal use only</footer>
    </div>
  )
}
