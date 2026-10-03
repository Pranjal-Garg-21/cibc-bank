import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useDeferredValue, useState } from 'react'
import { api, ApiError, qs } from '../api/client'
import type { Options, StaffRow } from '../api/types'
import { useAuth } from '../auth'
import { Badge, Button, FieldLabel, Spinner, cx, inputClass } from '../components/ui'
import { isoDate } from '../lib/format'
import { ROLE_LABEL, label } from '../lib/labels'

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

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-8">
          <p className="text-lg font-semibold tracking-tight">TrustIssues</p>
          <p className="text-sm text-muted">Collections decisioning · internal system</p>
        </div>
        <div className="rounded-lg border border-border bg-surface p-6">
          <h1 className="text-base">Sign in</h1>
          {notice && (
            <p role="status" className="mt-3 rounded-md border border-info/30 bg-info/10 px-3 py-2 text-sm text-info">
              {notice}
            </p>
          )}
          {options.isLoading && <Spinner />}
          {options.data && !options.data.demo_login && (
            <p className="mt-3 text-sm text-muted">
              Sign-in is not configured on this server. In the bank this page hands over to single sign-on.
            </p>
          )}
          {options.data?.demo_login && (
            <form
              className="mt-4"
              onSubmit={(e) => {
                e.preventDefault()
                if (picked) login.mutate(picked)
              }}
            >
              <div className="mb-4 flex items-start gap-2 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn">
                <Badge tone="warn">DEMO</Badge>
                <span>Demo sign-in: choose a member of staff. Production uses the bank's single sign-on with MFA.</span>
              </div>
              <FieldLabel htmlFor="staff-search">Find a member of staff</FieldLabel>
              <input
                id="staff-search"
                className={inputClass}
                placeholder="Name, id, role or team"
                autoComplete="off"
                maxLength={40}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <ul role="listbox" aria-label="Staff" className="mt-2 max-h-72 overflow-y-auto rounded-md border border-border">
                {staff.data?.staff.map((s) => (
                  <li
                    key={s.agent_id}
                    role="option"
                    aria-selected={picked === s.agent_id}
                    tabIndex={0}
                    onClick={() => setPicked(s.agent_id)}
                    onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setPicked(s.agent_id)}
                    className={cx(
                      'flex cursor-pointer items-center justify-between border-b border-border px-3 py-2 text-sm last:border-b-0',
                      picked === s.agent_id ? 'bg-accent/25' : 'hover:bg-raised',
                    )}
                  >
                    <span>
                      <span className="text-text">{s.name ?? s.agent_id}</span>
                      <span className="ml-2 font-mono text-xs text-faint">{s.agent_id}</span>
                    </span>
                    <span className="text-xs text-muted">
                      {label(ROLE_LABEL, s.role)}
                      {s.team_id ? ` · ${s.team_id}` : ''}
                    </span>
                  </li>
                ))}
                {staff.data && !staff.data.staff.length && <li className="px-3 py-3 text-sm text-muted">No match.</li>}
              </ul>
              {login.error && (
                <p role="alert" className="mt-3 text-sm text-danger">
                  {login.error instanceof ApiError ? login.error.message : 'Sign-in failed.'}
                </p>
              )}
              <Button type="submit" variant="primary" className="mt-4 w-full" disabled={!picked || login.isPending}>
                {login.isPending ? 'Signing in…' : 'Sign in'}
              </Button>
            </form>
          )}
        </div>
        <p className="mt-4 text-xs text-faint">
          {options.data && `Data as of ${isoDate(options.data.as_of_date)}. `}Authorised staff only. Access and activity are logged.
        </p>
      </div>
    </div>
  )
}
