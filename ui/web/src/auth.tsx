import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, onUnauthorized, setCsrfToken } from './api/client'
import type { Me } from './api/types'

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
      setCsrfToken(null)
      qc.clear() // nothing a previous user saw stays in memory (design §9.4)
      qc.setQueryData(['me'], null)
      setNotice(reason ?? null)
    },
    [qc],
  )

  const signOut = useCallback(
    async (reason?: string) => {
      try {
        await api.post('/api/session/logout')
      } catch {
        /* already gone */
      }
      clear(reason)
    },
    [clear],
  )

  // Only someone who was signed in is told their session ended; a first visit just shows the sign-in page.
  useEffect(
    () =>
      onUnauthorized(() => {
        if (qc.getQueryData(['me'])) clear('Your session has ended. Please sign in again.')
      }),
    [clear, qc],
  )

  return <AuthContext.Provider value={{ me, loading: q.isLoading, signOut, notice }}>{children}</AuthContext.Provider>
}

/** Idle lock (§9.1): warn two minutes before the server's idle timeout, then sign out. */
export function useIdleLock(idleMinutes: number) {
  const { signOut } = useAuth()
  const [warning, setWarning] = useState(false)
  const last = useRef(Date.now())
  useEffect(() => {
    const bump = () => {
      last.current = Date.now()
    }
    const events = ['pointerdown', 'keydown', 'scroll', 'wheel'] as const
    events.forEach((e) => window.addEventListener(e, bump, { passive: true }))
    const t = window.setInterval(() => {
      const idle = (Date.now() - last.current) / 60000
      if (idle >= idleMinutes) void signOut('You were signed out after a period of inactivity.')
      else setWarning(idle >= idleMinutes - 2)
    }, 5000)
    return () => {
      events.forEach((e) => window.removeEventListener(e, bump))
      window.clearInterval(t)
    }
  }, [idleMinutes, signOut])
  const stay = useCallback(async () => {
    last.current = Date.now()
    setWarning(false)
    await api.get('/api/session/me').catch(() => null) // touches the server session
  }, [])
  return { warning, stay }
}
