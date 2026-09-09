/**
 * useAdsPanel — derives the Safent Ads panel's origin from the owner-set
 * safent-ads managed-remote MCP URL (McpView's "Presets gestionados"
 * card). The ads service serves its own React panel at `/` on the SAME
 * host as the MCP bridge (`/mcp`), so the panel origin is just the MCP
 * URL's origin — never built from free text, always parsed out of a URL
 * the backend already validated (https-only, no IP literal, port 443;
 * see hermes.shell_server.managed_remote_endpoints).
 */
import { useEffect, useState } from 'react'
import { listManagedRemoteEndpoints } from '../api/client'

const SAFENT_ADS_SLUG = 'safent-ads'
const ADS_ENDPOINTS_POLL_MS = 15_000

/** Returns the https origin of *mcpUrl*, or null if it isn't a valid https URL. */
export function panelOriginFromMcpUrl(mcpUrl: string): string | null {
  try {
    const parsed = new URL(mcpUrl)
    return parsed.protocol === 'https:' ? parsed.origin : null
  } catch {
    return null
  }
}

/**
 * Polls GET /api/v1/mcp/managed-remote-endpoints for the safent-ads entry
 * and returns its panel origin, or null while unset/unreachable. Read-only,
 * no secrets (hostnames only) — same fail-soft poll shape as
 * usePendingApprovals: a transient error keeps the last known value instead
 * of flashing the nav item / view away.
 */
export function useAdsPanelOrigin(pollMs = ADS_ENDPOINTS_POLL_MS): string | null {
  const [origin, setOrigin] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const poll = () => {
      listManagedRemoteEndpoints()
        .then((res) => {
          if (!alive) return
          const url = res.endpoints?.[SAFENT_ADS_SLUG]
          setOrigin(url ? panelOriginFromMcpUrl(url) : null)
        })
        .catch(() => { /* transient — keep last known value */ })
    }
    poll()
    const id = setInterval(poll, pollMs)
    return () => { alive = false; clearInterval(id) }
  }, [pollMs])

  return origin
}
