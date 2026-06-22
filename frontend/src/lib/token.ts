// The shell-server injects window.__LUMEN_TOKEN__ into the served index.html on
// the ?k= bootstrap handshake. It is a SHORT-LIVED rotating session token (server
// TTL ~1h). We keep it in memory and renew it via POST /api/v1/session/refresh
// while the tab is active, so mutating API calls never 401 mid-session — without
// re-running the ?k= handshake and without exposing the bootstrap secret to the
// page (and thus to browser extensions).
let _token =
  ((window as unknown as Record<string, unknown>)['__LUMEN_TOKEN__'] as string) ?? ''

export const token = (): string => _token

/** Ask the server for a fresh rotating session token using the current valid one. */
export async function refreshToken(): Promise<boolean> {
  if (!_token) return false
  try {
    const res = await fetch('/api/v1/session/refresh', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${_token}`,
        'Content-Type': 'application/json',
      },
    })
    if (!res.ok) return false
    const data = (await res.json()) as { token?: string }
    if (data.token) {
      _token = data.token
      return true
    }
  } catch {
    /* transient network error — keep the current token, the timer retries */
  }
  return false
}

// Renew well before the server TTL (default 3600s) so an open tab never lapses.
const REFRESH_INTERVAL_MS = 45 * 60 * 1000
let _timer: ReturnType<typeof setInterval> | null = null

/** Start the periodic background refresh (call once at app startup). */
export function startTokenRefresh(): void {
  if (_timer || !_token) return
  _timer = setInterval(() => {
    void refreshToken()
  }, REFRESH_INTERVAL_MS)
}
