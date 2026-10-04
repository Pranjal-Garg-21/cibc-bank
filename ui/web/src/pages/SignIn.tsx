import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Eye, EyeOff, LockKeyhole, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '@/api/client'
import type { Options } from '@/api/types'
import { useAuth } from '@/auth'
import { ErrorState, Notice } from '@/components/app'
import { Wordmark } from '@/components/Wordmark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

/** One form for every member of staff: staff ID and password. The browser never chooses or sends a role; the server
 * reads it from the staff record and the app opens that role's first screen. No staff information is fetched or
 * shown before authentication. */
export function SignIn() {
  const { notice } = useAuth()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const options = useQuery({ queryKey: ['options'], queryFn: () => api.get<Options>('/api/session/options') })
  const [staffId, setStaffId] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [shown, setShown] = useState(false)
  const [capsLock, setCapsLock] = useState(false)
  const login = useMutation({
    mutationFn: () => api.post('/api/session/login', { staff_id: staffId.trim(), password, verification_code: code }),
    onSuccess: () => {
      navigate('/', { replace: true }) // the home route opens the signed-in role's first screen
      void qc.invalidateQueries({ queryKey: ['me'] })
      if ('BroadcastChannel' in window) {
        const channel = new BroadcastChannel('trustissues-session')
        channel.postMessage('signed-in')
        channel.close()
      }
    },
    onSettled: () => { setPassword(''); setCode(''); setShown(false) },
  })
  const o = options.data

  return (
    <div className="flex min-h-[100dvh] flex-col bg-panel">
      <header className="border-t-4 border-brand bg-white">
        <div className="console-width flex min-h-20 flex-wrap items-center justify-between gap-3 py-4">
          <Wordmark />
        </div>
        <div className="border-t border-border" />
      </header>

      <main className="console-width flex-1 py-8 md:py-16">
        <div className="sign-in-layout mx-auto max-w-[1000px]">
          <section className="sign-in-context">
            <h1 className="max-w-[12ch] text-[32px] leading-[1.15] tracking-tight md:text-[44px]">Your collections workspace</h1>
            <p className="mt-5 max-w-[40ch] text-[16px] leading-relaxed text-muted-foreground">Review your cases, support your team, and make informed collections decisions.</p>
            <div className="mt-8 max-w-[40ch] border-t border-border pt-6">
              <div className="flex items-center gap-2 text-sm font-bold"><ShieldCheck className="size-5 text-brand" strokeWidth={1.5} aria-hidden="true" />Protect your staff account</div>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">Keep passwords and verification codes private. The service desk will never ask you to share a sign-in code.</p>
              <p className="mt-4 text-xs leading-relaxed text-muted-foreground">This system is for authorized staff only. All activity is monitored and logged. Accounts lock after repeated failed attempts.</p>
            </div>
          </section>

          <section aria-labelledby="sign-in-heading" className="rounded-lg border border-border bg-white">
            <div className="border-b border-border px-6 pt-6 pb-5 sm:px-8">
              <div className="mb-2 flex items-center gap-2 text-[13px] text-muted-foreground"><LockKeyhole className="size-4" strokeWidth={1.5} aria-hidden="true" />Staff access</div>
              <h2 id="sign-in-heading" className="text-[26px] leading-tight">Sign in</h2>
            </div>
            <div className="px-6 py-6 sm:px-8">
              {notice && <div className="mb-5"><Notice tone="info">{notice}</Notice></div>}
              {options.error ? <ErrorState error={options.error} onRetry={() => options.refetch()} /> : o && !o.sign_in_configured ? (
                <Notice tone="warn" title="Sign-in is not set up on this server">Ask your administrator to create your account.</Notice>
              ) : (
                <form aria-label="Staff sign-in" onSubmit={(e) => { e.preventDefault(); if (staffId.trim() && password && !login.isPending) login.mutate() }}>
                  <p className="mb-5 text-sm text-muted-foreground">Use your staff ID and password. You will see the screens for your role.</p>
                  <div className="mb-5">
                    <Label htmlFor="staff-id" className="mb-2 text-sm font-bold">Staff ID</Label>
                    <Input id="staff-id" name="username" autoComplete="username" autoCapitalize="characters" spellCheck={false}
                      maxLength={40} required disabled={login.isPending} className="h-12 rounded-sm text-base" value={staffId} onChange={(e) => setStaffId(e.target.value)} />
                  </div>
                  <div className="mb-5">
                    <Label htmlFor="password" className="mb-2 text-sm font-bold">Password</Label>
                    <div className="relative">
                      <Input id="password" name="password" type={shown ? 'text' : 'password'} autoComplete="current-password" maxLength={128}
                        required disabled={login.isPending} className="h-12 rounded-sm pr-12 text-base" value={password} onChange={(e) => setPassword(e.target.value)}
                        onKeyUp={(e) => setCapsLock(e.getModifierState('CapsLock'))} aria-describedby={capsLock ? 'caps' : undefined} />
                      <button type="button" aria-label={shown ? 'Hide password' : 'Show password'} aria-pressed={shown} disabled={login.isPending}
                        className="absolute inset-y-0 right-0 flex w-12 items-center justify-center rounded-sm text-muted-foreground hover:text-brand" onClick={() => setShown((value) => !value)}>
                        {shown ? <EyeOff className="size-5" strokeWidth={1.5} /> : <Eye className="size-5" strokeWidth={1.5} />}
                      </button>
                    </div>
                    {capsLock && <p id="caps" className="mt-2 text-xs text-warn">Caps Lock is on.</p>}
                  </div>
                  {o?.mfa_required && <div className="mb-5">
                    <Label htmlFor="verification-code" className="mb-2 text-sm font-bold">Verification code</Label>
                    <Input id="verification-code" name="verification_code" autoComplete="one-time-code" spellCheck={false} autoCapitalize="none" required
                      disabled={login.isPending} maxLength={32} className="h-12 rounded-sm font-mono text-base" value={code} onChange={(e) => setCode(e.target.value)} aria-describedby="code-help" />
                    <p id="code-help" className="mt-2 text-xs text-muted-foreground">Enter the six-digit code from your authenticator, or an unused recovery code. For setup, contact the service desk.</p>
                  </div>}
                  {o?.demo && !o.mfa_required && <p className="mb-5 border-l-2 border-warn pl-3 text-xs leading-relaxed text-warn">{o.shared_access ? 'Temporary shared demo access. Use your staff ID and the demo password. No individual passwords are saved.' : 'Synthetic-data preview.'} Multi-factor authentication is enabled in deployment.</p>}
                  {login.error && <p role="alert" className="mb-5 rounded-sm border-l-4 border-danger bg-[#f5e3e8] px-3 py-3 text-sm text-danger">
                    {login.error instanceof ApiError ? login.error.message : 'Sign-in failed. Try again.'}
                  </p>}
                  <Button type="submit" size="lg" className="w-full justify-between" disabled={login.isPending || !o}>
                    {login.isPending ? 'Signing in…' : 'Sign in'}<ArrowRight aria-hidden="true" strokeWidth={1.5} />
                  </Button>
                  <p className="mt-5 text-[13px] leading-relaxed text-muted-foreground">Forgot your password or locked out? Agents: ask your team lead to reset it. Everyone else: contact the service desk.</p>
                </form>
              )}
            </div>
          </section>
        </div>
      </main>
      <footer className="border-t border-border bg-white py-5 text-xs text-muted-foreground"><div className="console-width">CIBC Collections · Collections decisioning · Internal use only</div></footer>
    </div>
  )
}
