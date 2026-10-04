// The only place the app talks to the server. Same origin, cookie session, CSRF header on every write.

export class ApiError extends Error {
  status: number
  code: string
  requestId: string | null
  constructor(status: number, code: string, message: string, requestId: string | null) {
    super(message)
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

let csrfToken: string | null = null
export function setCsrfToken(t: string | null) {
  csrfToken = t
}

type Listener = (e: ApiError) => void
const unauthorizedListeners = new Set<Listener>()
let reauthentication: (() => Promise<void>) | null = null
export function onReauthentication(fn: () => Promise<void>) {
  reauthentication = fn
  return () => { if (reauthentication === fn) reauthentication = null }
}
const pendingRequests = new Set<AbortController>()
let sessionGeneration = 0
export function abortSessionRequests() {
  sessionGeneration += 1
  pendingRequests.forEach((controller) => controller.abort())
}
/** Called when any request comes back 401, so the app can return to the sign-in screen. */
export function onUnauthorized(fn: Listener) {
  unauthorizedListeners.add(fn)
  return () => {
    unauthorizedListeners.delete(fn)
  }
}

async function request<T>(method: string, path: string, body?: unknown, retried = false, background = false): Promise<T> {
  const generation = sessionGeneration
  const headers: Record<string, string> = { Accept: 'application/json' }
  // An automatic refresh is not activity: the server does not reset the idle timer for it (tiui/deps.py).
  if (background) headers['X-TI-Background'] = '1'
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json'
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken
  }
  let res: Response
  const controller = new AbortController()
  pendingRequests.add(controller)
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
      signal: controller.signal,
    })
  } catch {
    if (controller.signal.aborted) throw new ApiError(0, 'session_changed', 'This request ended when your session changed.', null)
    throw new ApiError(0, 'network', 'The server cannot be reached. Check that it is running.', null)
  } finally {
    pendingRequests.delete(controller)
  }
  const data = await res.json().catch(() => null)
  if (generation !== sessionGeneration) throw new ApiError(0, 'session_changed', 'This request ended when your session changed.', null)
  if (!res.ok) {
    if (res.status === 403 && data?.code === 'reauthentication_required' && !retried && reauthentication) {
      // The server has refused before any write. Retry exactly once, after a successful fresh challenge.
      await reauthentication()
      return request<T>(method, path, body, true, background)
    }
    const err = new ApiError(
      res.status,
      data?.code ?? 'error',
      data?.message ?? 'Something went wrong.',
      data?.request_id ?? null,
    )
    if (res.status === 401 && !path.startsWith('/api/session/login')) unauthorizedListeners.forEach((fn) => fn(err))
    throw err
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  /** A read made by an automatic refresh (see `live`): it does not keep an idle session alive. */
  poll: <T>(path: string) => request<T>('GET', path, undefined, false, true),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
}

/** Lists that other people change (approvals, decision review, the queue): refetched every 20 s while the tab is
 * visible and whenever the window regains focus. Use with `api.poll` as the query function. */
export const live = { refetchInterval: 20_000, refetchIntervalInBackground: false, refetchOnWindowFocus: true } as const

/** Every query a decision, approval or batch approval can change, so the same browser updates at once. */
export const DECISION_QUERIES = [['case'], ['queue'], ['digest'], ['decision-review'], ['overview'], ['next-action'], ['summary']]

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}
