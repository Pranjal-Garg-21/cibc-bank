import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '@/api/client'
import type { Me } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export function ReauthenticateDialog({ me, onVerified, onCancel }: {
  me: Me; onVerified: (token: string) => void; onCancel: () => void
}) {
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const verify = useMutation({
    mutationFn: () => api.post<{ csrf_token: string }>('/api/session/reauthenticate', { password, verification_code: code }),
    onSuccess: (data) => onVerified(data.csrf_token),
    onSettled: () => { setPassword(''); setCode('') },
  })
  return (
    <Dialog open onOpenChange={(open) => !open && !verify.isPending && onCancel()}>
      <DialogContent className="sm:max-w-md" showCloseButton={!verify.isPending}>
        <DialogHeader>
          <DialogTitle className="text-xl">Confirm your identity</DialogTitle>
          <DialogDescription>Enter your credentials to continue this sensitive action. Staff ID {me.agent_id}.</DialogDescription>
        </DialogHeader>
        <form id="reauthenticate" className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (!verify.isPending) verify.mutate() }}>
          <div>
            <Label htmlFor="reauth-password" className="mb-2">Your password</Label>
            <Input id="reauth-password" type="password" autoComplete="current-password" autoFocus required maxLength={128}
              className="h-11" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {me.mfa && <div>
            <Label htmlFor="reauth-code" className="mb-2">Verification code</Label>
            <Input id="reauth-code" autoComplete="one-time-code" spellCheck={false} autoCapitalize="none" required maxLength={32}
              className="h-11 font-mono" value={code} onChange={(e) => setCode(e.target.value)} aria-describedby="reauth-code-help" />
            <p id="reauth-code-help" className="mt-2 text-xs text-muted-foreground">Use a fresh six-digit code or an unused recovery code.</p>
          </div>}
          {verify.error && <p role="alert" className="text-sm text-danger">{verify.error instanceof ApiError ? verify.error.message : 'Verification failed. Try again.'}</p>}
        </form>
        <DialogFooter>
          <Button variant="secondary" disabled={verify.isPending} onClick={onCancel}>Cancel</Button>
          <Button type="submit" form="reauthenticate" disabled={verify.isPending}>{verify.isPending ? 'Verifying…' : 'Verify and continue'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
