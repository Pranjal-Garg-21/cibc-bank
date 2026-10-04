import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { abortSessionRequests, api, onReauthentication, onUnauthorized, setCsrfToken } from './client'

const cleanups: (() => void)[] = []
beforeEach(() => { setCsrfToken(null); vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => { cleanups.splice(0).forEach((cleanup) => cleanup()); vi.unstubAllGlobals() })
const response = (status: number, body: unknown) => new Response(JSON.stringify(body), { status })

describe('session-bound API requests', () => {
  it('sends the session CSRF token on writes and never persists it', async () => {
    setCsrfToken('session-token')
    vi.mocked(fetch).mockResolvedValue(response(200, { ok: true }))
    await api.post('/api/team/agents/A1/revoke')
    const options = vi.mocked(fetch).mock.calls[0][1]!
    expect(options.credentials).toBe('same-origin')
    expect(options.cache).toBe('no-store')
    expect(options.headers).toMatchObject({ 'X-CSRF-Token': 'session-token' })
  })

  it('only retries a refused sensitive write after the user completes verification', async () => {
    setCsrfToken('old-token')
    vi.mocked(fetch).mockResolvedValueOnce(response(403, { code: 'reauthentication_required' }))
      .mockResolvedValueOnce(response(200, { ok: true }))
    const verify = vi.fn(async () => { setCsrfToken('new-token') })
    cleanups.push(onReauthentication(verify))
    await api.post('/api/team/agents/A1/revoke')
    expect(verify).toHaveBeenCalledOnce()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(vi.mocked(fetch).mock.calls[1][1]?.headers).toMatchObject({ 'X-CSRF-Token': 'new-token' })
  })

  it('does not retry a cancelled verification or an unrelated role denial', async () => {
    vi.mocked(fetch).mockResolvedValue(response(403, { code: 'reauthentication_required' }))
    cleanups.push(onReauthentication(async () => { throw new Error('Cancelled') }))
    await expect(api.post('/api/team/agents/A1/revoke')).rejects.toThrow('Cancelled')
    expect(fetch).toHaveBeenCalledOnce()
    vi.mocked(fetch).mockClear().mockResolvedValue(response(403, { code: 'forbidden' }))
    await expect(api.post('/api/team/agents/A1/revoke')).rejects.toMatchObject({ status: 403, code: 'forbidden' })
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('cannot loop when the retried request is still refused', async () => {
    vi.mocked(fetch).mockResolvedValue(response(403, { code: 'reauthentication_required' }))
    const verify = vi.fn(async () => {})
    cleanups.push(onReauthentication(verify))
    await expect(api.post('/api/team/agents/A1/revoke')).rejects.toMatchObject({ status: 403 })
    expect(verify).toHaveBeenCalledOnce()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('clears a session on 401 but leaves a failed sign-in on the sign-in screen', async () => {
    const listener = vi.fn()
    cleanups.push(onUnauthorized(listener))
    vi.mocked(fetch).mockResolvedValue(response(401, { code: 'sign_in_failed' }))
    await expect(api.post('/api/session/login')).rejects.toMatchObject({ status: 401 })
    expect(listener).not.toHaveBeenCalled()
    vi.mocked(fetch).mockResolvedValue(response(401, { code: 'not_signed_in' }))
    await expect(api.get('/api/status')).rejects.toMatchObject({ status: 401 })
    expect(listener).toHaveBeenCalledOnce()
  })

  it('discards responses from requests started under a previous session', async () => {
    let deliver!: (r: Response) => void
    vi.mocked(fetch).mockImplementation(() => new Promise((resolve) => { deliver = resolve }))
    const pending = api.get('/api/status')
    abortSessionRequests()
    deliver(response(200, { private_data: 'previous staff member' }))
    await expect(pending).rejects.toMatchObject({ code: 'session_changed' })
  })
})
