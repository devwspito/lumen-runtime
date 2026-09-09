import { describe, expect, it } from 'vitest'

import { panelOriginFromMcpUrl } from './useAdsPanel'

describe('panelOriginFromMcpUrl', () => {
  it('strips the /mcp path, keeping only the https origin', () => {
    expect(panelOriginFromMcpUrl('https://ads.example.com/mcp')).toBe('https://ads.example.com')
  })

  it('drops query/hash/trailing path segments along with the origin extraction', () => {
    expect(panelOriginFromMcpUrl('https://ads.example.com/mcp?token=abc#frag')).toBe('https://ads.example.com')
  })

  it('preserves a non-default port', () => {
    expect(panelOriginFromMcpUrl('https://ads.example.com:8443/mcp')).toBe('https://ads.example.com:8443')
  })

  it('rejects a non-https URL (never frames/links to http)', () => {
    expect(panelOriginFromMcpUrl('http://ads.example.com/mcp')).toBeNull()
  })

  it('rejects a scheme that is not a real URL scheme (e.g. javascript:)', () => {
    expect(panelOriginFromMcpUrl('javascript:alert(1)')).toBeNull()
  })

  it('rejects malformed input instead of throwing', () => {
    expect(panelOriginFromMcpUrl('not a url')).toBeNull()
    expect(panelOriginFromMcpUrl('')).toBeNull()
  })
})
