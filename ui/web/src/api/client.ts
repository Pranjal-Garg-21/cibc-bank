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
/** Called when any request comes back 401, so the app can return to the sign-in screen. */
export function onUnauthorized(fn: Listener) {
  unauthorizedListeners.add(fn)
  return () => {
    unauthorizedListeners.delete(fn)
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json'
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken
  }
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
    })
  } catch {
    throw new ApiError(0, 'network', 'The server cannot be reached. Check that it is running.', null)
  }
  const data = await res.json().catch(() => null)
  if (!res.ok) {
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
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}
