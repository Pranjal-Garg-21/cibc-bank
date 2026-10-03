import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '@/api/client'
import type { TeamAgent, TeamAgents } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, Loading, Notice, PageHeader, Section, StatusBadge } from '@/components/app'
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
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { dateTime } from '@/lib/format'
import { ROLE_LABEL, label } from '@/lib/labels'

/** Team leads set the sign-in password for each agent who reports to them. Agents cannot change it. */
export function TeamAccounts() {
  const me = useMe()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['team-agents'], queryFn: () => api.get<TeamAgents>('/api/team/agents') })
  const [editing, setEditing] = useState<TeamAgent | null>(null)
  const [revoking, setRevoking] = useState<TeamAgent | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const revoke = useMutation({
    mutationFn: (a: TeamAgent) => api.post(`/api/team/agents/${encodeURIComponent(a.agent_id)}/revoke`),
    onSuccess: (_d, a) => {
      setRevoking(null)
      setDone(`Sign-in removed for ${a.name ?? a.agent_id}. They have been signed out.`)
      void qc.invalidateQueries({ queryKey: ['team-agents'] })
    },
  })
  const agents = q.data?.agents ?? []
  const withAccount = agents.filter((a) => a.has_account).length

  return (
    <>
      <PageHeader
        title="Agent accounts"
        description={me.shared_access ? 'Temporary shared demo access is enabled for active staff. Individual passwords are not saved.' : 'Manage access for agents who report to you. Password resets and access removal end their active sessions.'}
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {done && (
        <div className="mb-4">
          <Notice tone="ok">{done}</Notice>
        </div>
      )}
      {q.data && (
        <Section title={`Your agents (${agents.length})`} description={`${withAccount} can sign in · team lead ${me.agent_id}`} flush>
          <DataTable
            data={agents as unknown as Record<string, unknown>[]}
            rowKey="agent_id"
            empty="No active agents report to you."
            caption="Your agents"
            columns={[
              { key: 'name', label: 'Name', render: (r) => <div><p className="font-bold">{String(r.name ?? r.agent_id)}</p><p className="mt-1 text-xs text-muted-foreground">{label(ROLE_LABEL, r.role as string)}</p></div> },
              { key: 'agent_id', label: 'Staff ID' },
              {
                key: 'has_account',
                label: 'Sign-in',
                render: (r) => (me.shared_access ? <StatusBadge tone="warn">Demo access</StatusBadge> : r.has_account ? <StatusBadge tone={!me.demo && !r.mfa_enrolled ? 'warn' : 'ok'}>{!me.demo && !r.mfa_enrolled ? 'MFA setup needed' : 'Password set'}</StatusBadge> : <StatusBadge>Not set up</StatusBadge>),
              },
              {
                key: 'set_at',
                label: 'Password set',
                render: (r) => (r.set_at ? <div><p>{dateTime(r.set_at)}</p><p className="mt-1 text-xs text-muted-foreground">By {r.set_by === me.agent_id ? 'you' : String(r.set_by ?? 'admin')}</p></div> : 'Not set'),
              },
              {
                key: 'actions',
                label: 'Actions',
                className: 'text-right',
                render: (r) => (
                  <span className="flex justify-end gap-2">
                    <Button size="sm" variant="outline" disabled={me.shared_access} onClick={() => (setDone(null), setEditing(r as unknown as TeamAgent))}>
                      {r.has_account ? 'Reset password' : 'Set password'}
                    </Button>
                    {Boolean(r.has_account) && (
                      <Button size="sm" variant="secondary" disabled={me.shared_access} onClick={() => (setDone(null), setRevoking(r as unknown as TeamAgent))}>
                        Remove access
                      </Button>
                    )}
                  </span>
                ),
              },
            ]}
          />
          <p className="border-t border-border px-5 py-4 text-[13px] text-muted-foreground">{me.shared_access ? 'Account changes are disabled during shared demo access. Existing passwords and authenticators are preserved.' : 'Agents cannot change their password. Use a secure channel to share a new password. Authenticator setup is handled by the service desk.'}</p>
        </Section>
      )}

      {editing && (
        <SetPasswordDialog
          agent={editing}
          onClose={() => setEditing(null)}
          onSaved={(a) => {
            setEditing(null)
            setDone(`Password saved for ${a.name ?? a.agent_id}. Give it to them in person or by a secure channel; it is not shown again.`)
            void qc.invalidateQueries({ queryKey: ['team-agents'] })
          }}
        />
      )}

      <AlertDialog open={!!revoking} onOpenChange={(o) => !o && setRevoking(null)}>
        <AlertDialogContent className="rounded-sm">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-normal">Remove sign-in for {revoking?.name ?? revoking?.agent_id}?</AlertDialogTitle>
            <AlertDialogDescription>
              Their password is deleted and they are signed out immediately. You can set a new password later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction disabled={revoke.isPending} onClick={(e) => (e.preventDefault(), revoking && revoke.mutate(revoking))}>
              Remove access
            </AlertDialogAction>
          </AlertDialogFooter>
          {revoke.error && <ErrorState error={revoke.error} />}
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.!@#%'

/** A random 16-character password from the browser's cryptographic generator. */
function generate(): string {
  const bytes = new Uint32Array(16)
  crypto.getRandomValues(bytes)
  const pw = Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join('')
  return /\d/.test(pw) && /[A-Za-z]/.test(pw) ? pw : generate()
}

function rules(pw: string): string | null {
  if (pw.length < 12) return 'Use at least 12 characters.'
  if (pw.length > 128) return 'Use at most 128 characters.'
  if (/^[A-Za-z]+$/.test(pw) || /^\d+$/.test(pw)) return 'Mix letters with digits or symbols.'
  return null
}

function SetPasswordDialog({ agent, onClose, onSaved }: { agent: TeamAgent; onClose: () => void; onSaved: (a: TeamAgent) => void }) {
  const [pw, setPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [shown, setShown] = useState(false)
  const save = useMutation({
    mutationFn: () => api.post(`/api/team/agents/${encodeURIComponent(agent.agent_id)}/password`, { password: pw }),
    onSuccess: () => onSaved(agent),
  })
  const problem = pw ? rules(pw) : null
  const mismatch = confirm && confirm !== pw
  const ready = pw && !problem && confirm === pw

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="rounded-sm sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-normal">
            {agent.has_account ? 'Reset password' : 'Set password'} for {agent.name ?? agent.agent_id}
          </DialogTitle>
          <DialogDescription>
            Staff ID {agent.agent_id}. This becomes their fixed sign-in password; they cannot change it.
            {agent.has_account && ' Their current password stops working and all their sessions end at once. Their authenticator remains enrolled.'}
          </DialogDescription>
        </DialogHeader>
        <form
          id="set-pw"
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) save.mutate()
          }}
        >
          <div>
            <Label htmlFor="new-pw" className="mb-1.5 text-[14px] font-bold">
              New password
            </Label>
            <Input
              id="new-pw"
              type={shown ? 'text' : 'password'}
              autoComplete="new-password"
              maxLength={128}
              className="h-10 rounded-sm"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              aria-invalid={!!problem}
            />
            <p className={`mt-1 text-xs ${problem ? 'text-danger' : 'text-muted-foreground'}`}>
              {problem ?? 'At least 12 characters, mixing letters with digits or symbols.'}
            </p>
          </div>
          <div>
            <Label htmlFor="confirm-pw" className="mb-1.5 text-[14px] font-bold">
              Confirm password
            </Label>
            <Input
              id="confirm-pw"
              type={shown ? 'text' : 'password'}
              autoComplete="new-password"
              maxLength={128}
              className="h-10 rounded-sm"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              aria-invalid={!!mismatch}
            />
            {mismatch && <p className="mt-1 text-xs text-danger">The passwords do not match.</p>}
          </div>
          <div className="flex items-center gap-4 text-sm">
            <Button
              type="button"
              variant="link"
              size="sm"
              className="px-0"
              onClick={() => {
                const g = generate()
                setPw(g)
                setConfirm(g)
                setShown(true)
              }}
            >
              Generate a password
            </Button>
            <label className="flex items-center gap-2 text-muted-foreground">
              <input type="checkbox" checked={shown} onChange={(e) => setShown(e.target.checked)} className="accent-[var(--brand)]" />
              Show password
            </label>
          </div>
          {save.error && (
            <p role="alert" className="border-l-4 border-danger bg-[#f5e3e8] px-3 py-2 text-sm text-danger">
              {save.error instanceof ApiError ? save.error.message : 'Could not save the password.'}
            </p>
          )}
        </form>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="set-pw" disabled={!ready || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save password'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
