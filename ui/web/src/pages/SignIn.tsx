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

/**
 * Staff sign-in: staff ID and password. Nothing about any member of staff is shown or fetched before sign-in.
 * (In the bank this page would hand over to single sign-on.)
 */
export function SignIn() {
  const { notice } = useAuth()
  const qc = useQueryClient()
  const options = useQuery({ queryKey: ['options'], queryFn: () => api.get<Options>('/api/session/options') })
  const [staffId, setStaffId] = useState('')
  const [password, setPassword] = useState('')
  const [capsLock, setCapsLock] = useState(false)
  const login = useMutation({
    mutationFn: () => api.post('/api/session/login', { staff_id: staffId.trim(), password }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['me'] }),
    onSettled: () => setPassword(''), // never keep the password in memory longer than needed
  })
  const o = options.data

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
          <h1 className="mb-1 text-[28px]">Sign in</h1>
          <p className="mb-6 text-muted-foreground">Use your staff ID and password.</p>

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
            <form
              className="border border-border bg-white p-6"
              onSubmit={(e) => {
                e.preventDefault()
                if (staffId.trim() && password) login.mutate()
              }}
            >
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
                {login.isPending ? 'Signing in…' : 'Sign in'}
              </Button>
              <p className="mt-4 text-[13px] text-muted-foreground">
                Forgot your password or locked out? Contact your team lead or the service desk.
              </p>
            </form>
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
