/**
 * AddProviderInline — self-contained "connect a provider" widget.
 *
 * Reused by the onboarding wizard (Step 1) without duplicating the
 * add/activate/test flow that already lives in ProvidersView.
 *
 * Props:
 *   provider   — a native Provider entry from listNativeProviders()
 *   onSuccess  — called after add + activate + test succeeds
 *   onError    — called with a human-readable error string
 */

import { useEffect, useRef, useState } from 'react'
import {
  addProvider,
  setActiveProvider,
  testProvider,
  startProviderOAuth,
  getProviderOAuthStatus,
} from '../api/client'
import type { Provider } from '../api/types'

// Mirrors the same logic in ProvidersView
const OAUTH_IDS = new Set(['nous', 'openai-codex', 'xai-oauth'])

function isOAuthProvider(p: Provider): boolean {
  return Boolean(p.supports_oauth)
    || /oauth/i.test(String(p.auth_type ?? ''))
    || OAUTH_IDS.has(p.provider_id ?? '')
}

type VerifyState =
  | { phase: 'idle' }
  | { phase: 'saving' }
  | { phase: 'testing' }
  | { phase: 'ok' }
  | { phase: 'fail'; message: string }
  | { phase: 'oauth-pending' }

interface Props {
  provider: Provider
  onSuccess(): void
  onError(message: string): void
}

export default function AddProviderInline({ provider, onSuccess, onError }: Props) {
  const [apiKey, setApiKey] = useState('')
  const [verify, setVerify] = useState<VerifyState>({ phase: 'idle' })
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const id = provider.provider_id ?? ''
  const name = provider.alias ?? provider.name ?? id
  const isOAuth = isOAuthProvider(provider)

  useEffect(() => {
    if (!isOAuth) inputRef.current?.focus()
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current)
    }
  }, [isOAuth])

  async function handleConfirm() {
    if (!apiKey.trim()) {
      setVerify({ phase: 'fail', message: 'Pega la API key antes de continuar.' })
      return
    }
    setVerify({ phase: 'saving' })
    try {
      await addProvider({
        provider_id: id,
        alias: provider.alias ?? provider.name,
        api_key: apiKey.trim(),
        kind: provider.kind ?? provider.category,
      })
      await setActiveProvider(id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'No se pudo guardar el proveedor.'
      setVerify({ phase: 'fail', message: msg })
      onError(msg)
      return
    }

    setVerify({ phase: 'testing' })
    try {
      const r = await testProvider(id)
      if (r?.ok) {
        setVerify({ phase: 'ok' })
        onSuccess()
      } else {
        setVerify({ phase: 'fail', message: 'La API key no funciona — revísala e inténtalo de nuevo.' })
      }
    } catch {
      setVerify({ phase: 'fail', message: 'La API key no funciona — revísala e inténtalo de nuevo.' })
    }
  }

  async function handleOAuth() {
    setVerify({ phase: 'oauth-pending' })
    let r: Record<string, unknown>
    try {
      r = await startProviderOAuth(id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'No se pudo iniciar la conexión.'
      setVerify({ phase: 'fail', message: msg })
      onError(msg)
      return
    }

    if (!r || r['error']) {
      const msg = `No se pudo conectar: ${(r?.['error'] as string) ?? 'unknown'}`
      setVerify({ phase: 'fail', message: msg })
      onError(msg)
      return
    }

    const session = r['session_id'] as string | undefined
    const url = (r['auth_url'] ?? r['verification_url']) as string | undefined

    if (url) window.open(url, '_blank', 'noopener,noreferrer')
    if (!session) {
      setVerify({ phase: 'fail', message: 'La sesión no devolvió un ID — inténtalo de nuevo.' })
      return
    }

    const intervalMs = Math.max(2000, ((r['poll_interval'] as number | undefined) ?? 4) * 1000)
    const deadline = Date.now() + Math.max(60, ((r['expires_in'] as number | undefined) ?? 600)) * 1000

    const poll = async () => {
      if (Date.now() > deadline) {
        setVerify({ phase: 'fail', message: 'La sesión expiró — vuelve a intentarlo.' })
        return
      }
      const st = await getProviderOAuthStatus(session)
      const status = String(st?.status ?? '').toLowerCase()
      if (status === 'approved' || status === 'connected' || status === 'success') {
        setVerify({ phase: 'ok' })
        onSuccess()
        return
      }
      if (status === 'error' || status === 'failed' || status === 'expired') {
        setVerify({ phase: 'fail', message: st?.error_message ?? st?.error ?? 'No se pudo conectar.' })
        return
      }
      pollRef.current = setTimeout(poll, intervalMs)
    }
    pollRef.current = setTimeout(poll, intervalMs)
  }

  const isBusy = verify.phase === 'saving' || verify.phase === 'testing' || verify.phase === 'oauth-pending'

  return (
    <div className="ob-add-provider" aria-live="polite">
      {verify.phase === 'ok' ? (
        <div className="ob-verify ob-verify--ok" role="status">
          <span className="ob-verify__icon" aria-hidden="true">✓</span>
          <span>Tu modelo responde correctamente — listo para chatear.</span>
        </div>
      ) : (
        <>
          {isOAuth ? (
            <button
              className="cv-btn cv-btn--primary"
              style={{ width: '100%' }}
              onClick={handleOAuth}
              disabled={isBusy}
              type="button"
            >
              {verify.phase === 'oauth-pending' ? 'Esperando autorización…' : `Conectar con ${name}`}
            </button>
          ) : (
            <div className="cv-form-stack">
              <label className="cv-label" htmlFor={`ob-key-${id}`}>
                API key de {name}
              </label>
              <input
                id={`ob-key-${id}`}
                ref={inputRef}
                className="cv-input"
                type="password"
                autoComplete="new-password"
                placeholder="Pega tu API key aquí"
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !isBusy) void handleConfirm() }}
                disabled={isBusy}
                aria-describedby={verify.phase === 'fail' ? `ob-err-${id}` : undefined}
              />
              {verify.phase === 'fail' && (
                <p id={`ob-err-${id}`} className="ob-field-error" role="alert">
                  {verify.message}
                </p>
              )}
              <p className="cv-hint">
                La key se guarda solo en Lumen — nunca sale del dispositivo.
              </p>
              <button
                className="cv-btn cv-btn--primary"
                style={{ alignSelf: 'flex-start' }}
                onClick={handleConfirm}
                disabled={isBusy || !apiKey.trim()}
                type="button"
              >
                {verify.phase === 'saving' && 'Guardando…'}
                {verify.phase === 'testing' && 'Verificando conexión…'}
                {(verify.phase === 'idle' || verify.phase === 'fail') && 'Guardar y verificar'}
              </button>
            </div>
          )}

          {verify.phase === 'fail' && isOAuth && (
            <p className="ob-field-error" role="alert" style={{ marginTop: 'var(--sp-2)' }}>
              {verify.message}
            </p>
          )}
        </>
      )}
    </div>
  )
}
