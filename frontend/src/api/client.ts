import { token } from '../lib/token'
import type { Agent, ActiveAgentResponse, RuntimeStatus } from './types'

// Mirrors the timeout strategy in vanilla api.js: snappy GETs fail fast;
// long-running mutations get explicit larger timeouts.
const DEFAULT_TIMEOUT_MS = 20_000
const BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, headers: extraHeaders, ...rest } = options

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(extraHeaders as Record<string, string> ?? {}),
  }

  const tok = token()
  if (tok && !headers['Authorization']) {
    headers['Authorization'] = `Bearer ${tok}`
  }

  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { ...rest, headers, signal: ctrl.signal })
  } catch (err) {
    clearTimeout(timer)
    const e = err as Error
    if (e.name === 'AbortError') {
      throw new ApiError(
        `La petición tardó demasiado (${Math.round(timeoutMs / 1000)}s) y se canceló.`,
        0,
        null,
      )
    }
    throw new ApiError(`Error de red: ${e.message}`, 0, null)
  }
  clearTimeout(timer)

  if (!res.ok) {
    let body: unknown = null
    try { body = await res.json() } catch { /* non-JSON */ }
    const b = body as Record<string, unknown> | null
    const message =
      (b?.detail as Record<string, unknown> | undefined)?.message as string
      ?? b?.detail as string
      ?? `HTTP ${res.status}`
    throw new ApiError(message, res.status, body)
  }

  if (res.status === 204) return null as T

  const json = await res.json() as Record<string, unknown>

  // Mirror the vanilla api.js {ok:false} guard (mutators return 2xx with ok:false
  // on daemon-level failures — e.g. addMcpServer).
  if (json['ok'] === false) {
    throw new ApiError(
      (json['error'] as string | undefined) ?? 'La operación falló.',
      res.status,
      json,
    )
  }

  return json as T
}

// ── Agents ────────────────────────────────────────────────────────────────────

export function listAgents(): Promise<Agent[]> {
  return request<Agent[]>('/agents').catch(() => [])
}

export function getActiveAgent(): Promise<ActiveAgentResponse> {
  return request<ActiveAgentResponse>('/agents/active').catch(
    () => ({ active_agent_id: '' }),
  )
}

// ── Runtime ───────────────────────────────────────────────────────────────────

export function getRuntimeStatus(): Promise<RuntimeStatus> {
  return request<RuntimeStatus>('/runtime/status').catch(
    () => ({ state: 'unknown', active_task_count: 0 }),
  )
}
