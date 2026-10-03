import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { abortSessionRequests, api, ApiError, onReauthentication, onUnauthorized, setCsrfToken } from './api/client'
import type { Me, SessionState } from './api/types'
const ReauthenticateDialog = lazy(() => import('@/components/ReauthenticateDialog').then((module) => ({ default: module.ReauthenticateDialog })))

type Auth = { me: Me | null; loading: boolean; signOut: (reason?: string) => Promise<void>; notice: string | null }
const AuthContext = createContext<Auth>({ me: null, loading: true, signOut: async () => {}, notice: null })

export function useAuth() {
  return useContext(AuthContext)
}

export function useMe(): Me {
  const { me } = useAuth()
  if (!me) throw new Error('useMe outside a signed-in page')
  return me
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)
  const [verificationOpen, setVerificationOpen] = useState(false)
  const challenge = useRef<{ promise: Promise<void>; resolve: () => void; reject: (error: Error) => void } | null>(null)
  const channel = useRef<BroadcastChannel | null>(null)
  const q = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api.get<Me>('/api/session/me')
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    retry: false,
    staleTime: Infinity,
  })
  const me = q.data ?? null
  useEffect(() => setCsrfToken(me?.csrf_token ?? null), [me])

  const clear = useCallback(
    (reason?: string) => {
      abortSessionRequests()
      setCsrfToken(null)
      void qc.cancelQueries()
      // Keep the active auth observer attached; remove every customer-data query and replace the identity.
      qc.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' })
      qc.setQueryData(['me'], null)
      setNotice(reason ?? null)
      challenge.current?.reject(new ApiError(401, 'not_signed_in', 'Your session has ended.', null))
      challenge.current = null
      setVerificationOpen(false)
    },
    [qc],
  )

  const signOut = useCallback(
    async (reason?: string) => {
      let message = reason
      try {
        await api.post('/api/session/logout')
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 401)) {
          message = 'This screen has been cleared. The server could not confirm sign-out; close this browser.'
        }
      }
      clear(message)
      channel.current?.postMessage('signed-out') // no identity or customer data sent between tabs
    },
    [clear],
  )

  useEffect(() => {
    if (!('BroadcastChannel' in window)) return
    const c = new BroadcastChannel('trustissues-session')
    channel.current = c
    c.onmessage = (event) => {
      if (event.data === 'signed-out') clear('You signed out in another tab.')
      if (event.data === 'signed-in') {
        clear()
        void qc.invalidateQueries({ queryKey: ['me'] })
      }
      if (event.data === 'session-renewed') void qc.invalidateQueries({ queryKey: ['me'] })
    }
    return () => { c.close(); channel.current = null }
  }, [clear, qc])

  useEffect(() => onReauthentication(() => {
    if (challenge.current) return challenge.current.promise
    let resolve!: () => void
    let reject!: (error: Error) => void
    const promise = new Promise<void>((done, fail) => { resolve = done; reject = fail })
    challenge.current = { promise, resolve, reject }
    setVerificationOpen(true)
    return promise
  }), [])

  // Only someone who was signed in is told their session ended; a first visit just shows the sign-in page.
  useEffect(
    () =>
      onUnauthorized(() => {
        if (qc.getQueryData(['me'])) clear('Your session has ended. Please sign in again.')
      }),
    [clear, qc],
  )

  return <AuthContext.Provider value={{ me, loading: q.isLoading, signOut, notice }}>
    {children}
    {verificationOpen && me && <Suspense fallback={null}><ReauthenticateDialog me={me} onVerified={(token) => {
      setCsrfToken(token)
      qc.setQueryData<Me>(['me'], (old) => old ? { ...old, csrf_token: token } : old)
      channel.current?.postMessage('session-renewed')
      challenge.current?.resolve()
      challenge.current = null
      setVerificationOpen(false)
    }} onCancel={() => {
      challenge.current?.reject(new ApiError(403, 'verification_cancelled', 'The action was cancelled. No change was made.', null))
      challenge.current = null
      setVerificationOpen(false)
    }} /></Suspense>}
  </AuthContext.Provider>
}

/** Idle lock (§9.1): warn two minutes before the server's idle timeout, then sign out. */
export function useIdleLock(idleMinutes: number | null, maxHours: number | null) {
  const { signOut } = useAuth()
  const [warning, setWarning] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState<number | null>(idleMinutes === null && maxHours === null ? null
    : Math.min(idleMinutes === null ? Infinity : idleMinutes * 60, maxHours === null ? Infinity : maxHours * 3600))
  // The browser's wall clock can differ from the server. Anchor the server's remaining durations to a
  // monotonic clock; never compare signed_in_at with Date.now() to decide whether to sign someone out.
  const deadlines = useRef({ idle: idleMinutes === null ? Infinity : performance.now() + idleMinutes * 60000,
    absolute: maxHours === null ? Infinity : performance.now() + maxHours * 3600000 })
  const warningRef = useRef(false)
  const lastKeepalive = useRef(0)
  const ending = useRef(false)
  const sync = useCallback((state: SessionState) => {
    const now = performance.now()
    deadlines.current = { idle: state.idle_seconds_left === null ? Infinity : now + state.idle_seconds_left * 1000,
      absolute: state.absolute_seconds_left === null ? Infinity : now + state.absolute_seconds_left * 1000 }
  }, [])
  useEffect(() => {
    let disposed = false
    let lastPoll = 0
    const poll = () => {
      lastPoll = performance.now()
      void api.get<SessionState>('/api/session/state').then((state) => { if (!disposed) sync(state) }).catch(() => null)
    }
    const bump = () => {
      if (document.visibilityState !== 'visible' || warningRef.current || ending.current || performance.now() - lastKeepalive.current < 60000) return
      lastKeepalive.current = performance.now()
      void api.post<SessionState>('/api/session/keepalive').then((state) => { if (!disposed) sync(state) }).catch(() => null)
    }
    const events = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const
    events.forEach((e) => window.addEventListener(e, bump, { passive: true }))
    const visibility = () => { if (document.visibilityState === 'visible') poll() }
    document.addEventListener('visibilitychange', visibility)
    poll()
    const t = window.setInterval(() => {
      const now = performance.now()
      const deadline = Math.min(deadlines.current.idle, deadlines.current.absolute)
      const remaining = Number.isFinite(deadline) ? Math.max(0, Math.ceil((deadline - now) / 1000)) : null
      setSecondsLeft(remaining)
      warningRef.current = remaining !== null && remaining <= 120
      setWarning(warningRef.current)
      if (remaining === 0 && !ending.current) {
        ending.current = true
        void signOut('Your session expired. Please sign in again.')
      } else if (now - lastPoll >= 30000 && document.visibilityState === 'visible') poll()
    }, 1000)
    return () => {
      disposed = true
      events.forEach((e) => window.removeEventListener(e, bump))
      document.removeEventListener('visibilitychange', visibility)
      window.clearInterval(t)
    }
  }, [signOut, sync])
  const stay = useCallback(async () => {
    const state = await api.post<SessionState>('/api/session/keepalive').catch(() => null)
    if (state) {
      sync(state)
      warningRef.current = Math.min(state.idle_seconds_left ?? Infinity, state.absolute_seconds_left ?? Infinity) <= 120
      setWarning(warningRef.current)
    }
  }, [sync])
  return { warning, stay, secondsLeft }
}
