/**
 * OnboardingView — guided first-run wizard (3 steps).
 *
 * Step 0: Entry screen — "Bienvenido a Lumen"
 * Step 1: Connect a model (required to advance to Finish)
 * Step 2: Your team (optional, skippable)
 * Step 3: Start — prompts + "Open chat"
 *
 * Routing: rendered at /bienvenida.
 * Gate: App.tsx redirects here when no provider is active.
 *
 * When the user completes or skips, we call onDone() which reloads the
 * provider state in the parent and navigates to /chat.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { sileo } from 'sileo'
import { listNativeProviders } from '../api/client'
import type { Provider } from '../api/types'
import AddProviderInline from '../components/AddProviderInline'

// ── Provider catalogue helpers ────────────────────────────────────────────────

/** Human-readable hint shown under each provider option in the wizard */
function providerHint(p: Provider): string {
  const a = String(p.auth_type ?? '').toLowerCase()
  if (/oauth/i.test(a) || Boolean(p.supports_oauth)) return 'Inicio de sesión — sin API key'
  const name = (p.alias ?? p.name ?? '').toLowerCase()
  if (name.includes('ollama') || name.includes('vllm') || name.includes('lm studio')) {
    return 'Modelo local — sin suscripción'
  }
  return 'De pago — requiere API key'
}

/** Sort order: prioritise well-known cloud providers first */
const PRIORITY: Record<string, number> = {
  anthropic: 1, openai: 2, google: 3, gemini: 3,
  groq: 4, mistral: 5, nous: 6,
}

function priorityOf(p: Provider): number {
  const k = String(p.kind ?? p.category ?? '').toLowerCase()
  const n = String(p.name ?? p.alias ?? '').toLowerCase()
  return PRIORITY[k] ?? PRIORITY[n] ?? 99
}

/** Filter out "advanced" (local/compatible) providers for the simplified list */
function isAdvanced(p: Provider): boolean {
  const k = String(p.kind ?? p.category ?? '').toLowerCase()
  return k === 'openai_compatible' || k === 'vllm' || k === 'ollama'
}

// ── Catalogue loading state ───────────────────────────────────────────────────

type CatalogueState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; providers: Provider[]; advanced: Provider[] }

type CatalogueAction =
  | { type: 'LOADED'; providers: Provider[]; advanced: Provider[] }
  | { type: 'FAILED'; message: string }
  | { type: 'RETRY' }

function catalogueReducer(_s: CatalogueState, a: CatalogueAction): CatalogueState {
  switch (a.type) {
    case 'LOADED': return { status: 'ready', providers: a.providers, advanced: a.advanced }
    case 'FAILED': return { status: 'error', message: a.message }
    case 'RETRY':  return { status: 'loading' }
  }
}

// ── Stepper types ─────────────────────────────────────────────────────────────

type StepId = 0 | 1 | 2 | 3

interface StepperProps {
  current: StepId
  modelConnected: boolean
}

function Stepper({ current, modelConnected }: StepperProps) {
  const steps = [
    { id: 1, label: 'Conecta un modelo' },
    { id: 2, label: 'Tu equipo' },
    { id: 3, label: 'Empieza' },
  ]
  return (
    <div className="ob-stepper" aria-label="Pasos del asistente de configuración">
      {steps.map((s, idx) => {
        const done = (s.id === 1 && modelConnected) || (current > s.id)
        const active = current === s.id
        return (
          <div
            key={s.id}
            className={[
              'ob-step',
              active ? 'ob-step--active' : '',
              done ? 'ob-step--done' : '',
            ].filter(Boolean).join(' ')}
          >
            <div className="ob-step__circle" aria-hidden="true">
              {done ? '✓' : s.id}
            </div>
            <span className="ob-step__label">{s.label}</span>
            {idx < steps.length - 1 && (
              <div className="ob-step__connector" aria-hidden="true" />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Quick suggestion prompts used in Step 3 ───────────────────────────────────

const STARTER_PROMPTS = [
  'Investiga los mejores CRMs para una startup B2B',
  'Redacta un email de propuesta comercial',
  'Organiza mis tareas de esta semana en un plan de acción',
  'Resume este documento en 5 puntos clave',
]

// ── OnboardingView ────────────────────────────────────────────────────────────

interface Props {
  onDone(): void
}

export default function OnboardingView({ onDone }: Props) {
  const navigate = useNavigate()
  const [step, setStep] = useState<StepId>(0)
  const [modelConnected, setModelConnected] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState<Provider | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [catalogue, dispatchCatalogue] = useReducer(catalogueReducer, { status: 'loading' })
  const headingRef = useRef<HTMLHeadingElement>(null)

  // Focus the heading whenever the step changes (accessibility)
  useEffect(() => {
    headingRef.current?.focus()
  }, [step])

  const loadCatalogue = useCallback(() => {
    dispatchCatalogue({ type: 'RETRY' })
    listNativeProviders()
      .then(raw => {
        const all = Array.isArray(raw) ? raw : []
        const sorted = [...all].sort((a, b) => priorityOf(a) - priorityOf(b))
        const simple = sorted.filter(p => !isAdvanced(p))
        const advanced = sorted.filter(p => isAdvanced(p))
        dispatchCatalogue({ type: 'LOADED', providers: simple, advanced })
      })
      .catch(err => {
        dispatchCatalogue({
          type: 'FAILED',
          message: err instanceof Error ? err.message : 'No se pudo cargar el catálogo.',
        })
      })
  }, [])

  useEffect(() => { loadCatalogue() }, [loadCatalogue])

  function handleSuggestion(text: string) {
    // Store the prompt for ChatView to pick up, then navigate
    sessionStorage.setItem('lumen_starter_prompt', text)
    onDone()
  }

  function handleOpenChat() {
    onDone()
  }

  function handleSkipLater() {
    // Allow "do it later" — the sidebar badge will remind them
    navigate('/chat', { replace: true })
  }

  function handleModelSuccess() {
    setModelConnected(true)
    sileo.success({ title: 'Modelo conectado y verificado' })
    // Auto-advance after a short beat so the user sees the green confirmation
    setTimeout(() => setStep(2), 900)
  }

  function handleModelError(message: string) {
    sileo.error({ title: message })
  }

  // ── Step 0: Entry ──────────────────────────────────────────────────────────

  if (step === 0) {
    return (
      <div className="ob-shell" role="main">
        <div className="ob-card ob-card--wide ob-entry">
          <div className="ob-entry__mark" aria-hidden="true">L</div>
          <h1 className="ob-entry__title" ref={headingRef} tabIndex={-1}>
            Bienvenido a Lumen
          </h1>
          <p className="ob-entry__subtitle">
            En 2 minutos conectamos tu modelo y te dejamos listo para empezar.
          </p>
          <button
            className="cv-btn cv-btn--primary ob-entry__cta"
            onClick={() => setStep(1)}
            type="button"
          >
            Empezar
          </button>
          <button
            className="ob-skip-link"
            onClick={handleSkipLater}
            type="button"
          >
            Hacerlo luego
          </button>
        </div>
      </div>
    )
  }

  // ── Step 1: Connect a model ───────────────────────────────────────────────

  if (step === 1) {
    const providers = catalogue.status === 'ready' ? catalogue.providers : []
    const advancedProviders = catalogue.status === 'ready' ? catalogue.advanced : []

    return (
      <div className="ob-shell" role="main">
        <div className="ob-card">
          <Stepper current={1} modelConnected={modelConnected} />

          <div className="ob-step-content">
            <h1 className="ob-step__title" ref={headingRef} tabIndex={-1}>
              Conecta un modelo de IA
            </h1>
            <p className="ob-step__desc">
              Lumen necesita un modelo para pensar. Elige uno de la lista y pega tu API key.
            </p>

            {catalogue.status === 'loading' && (
              <div className="state-container" aria-busy="true" aria-live="polite">
                <p className="state-label">Cargando opciones…</p>
              </div>
            )}

            {catalogue.status === 'error' && (
              <div className="state-container" role="alert">
                <p className="state-error">{catalogue.message}</p>
                <button className="cv-btn cv-btn--secondary" onClick={loadCatalogue} type="button">
                  Reintentar
                </button>
              </div>
            )}

            {catalogue.status === 'ready' && (
              <>
                <ul className="ob-provider-list" role="list" aria-label="Modelos disponibles">
                  {providers.map(p => {
                    const pid = p.provider_id ?? ''
                    const pname = p.alias ?? p.name ?? pid
                    const isSelected = selectedProvider?.provider_id === pid
                    return (
                      <li key={pid}>
                        <button
                          className={['ob-provider-btn', isSelected ? 'ob-provider-btn--selected' : ''].filter(Boolean).join(' ')}
                          onClick={() => setSelectedProvider(isSelected ? null : p)}
                          type="button"
                          aria-pressed={isSelected}
                          aria-expanded={isSelected}
                        >
                          <span className="ob-provider-btn__name">{pname}</span>
                          <span className="ob-provider-btn__hint">{providerHint(p)}</span>
                          <span className="ob-provider-btn__chevron" aria-hidden="true">
                            {isSelected ? '▲' : '▼'}
                          </span>
                        </button>

                        {isSelected && (
                          <div className="ob-provider-form" role="region" aria-label={`Configurar ${pname}`}>
                            <AddProviderInline
                              provider={p}
                              onSuccess={handleModelSuccess}
                              onError={handleModelError}
                            />
                          </div>
                        )}
                      </li>
                    )
                  })}

                  {advancedProviders.length > 0 && (
                    <li>
                      <button
                        className="ob-advanced-toggle"
                        onClick={() => setShowAdvanced(v => !v)}
                        type="button"
                        aria-expanded={showAdvanced}
                      >
                        {showAdvanced ? 'Ocultar opciones avanzadas' : 'Tengo un modelo propio o local'}
                      </button>

                      {showAdvanced && advancedProviders.map(p => {
                        const pid = p.provider_id ?? ''
                        const pname = p.alias ?? p.name ?? pid
                        const isSelected = selectedProvider?.provider_id === pid
                        return (
                          <div key={pid}>
                            <button
                              className={['ob-provider-btn ob-provider-btn--advanced', isSelected ? 'ob-provider-btn--selected' : ''].filter(Boolean).join(' ')}
                              onClick={() => setSelectedProvider(isSelected ? null : p)}
                              type="button"
                              aria-pressed={isSelected}
                              aria-expanded={isSelected}
                            >
                              <span className="ob-provider-btn__name">{pname}</span>
                              <span className="ob-provider-btn__hint">{providerHint(p)}</span>
                              <span className="ob-provider-btn__chevron" aria-hidden="true">
                                {isSelected ? '▲' : '▼'}
                              </span>
                            </button>
                            {isSelected && (
                              <div className="ob-provider-form" role="region" aria-label={`Configurar ${pname}`}>
                                <AddProviderInline
                                  provider={p}
                                  onSuccess={handleModelSuccess}
                                  onError={handleModelError}
                                />
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </li>
                  )}
                </ul>

                {modelConnected && (
                  <button
                    className="cv-btn cv-btn--primary ob-step__next"
                    onClick={() => setStep(2)}
                    type="button"
                  >
                    Continuar
                  </button>
                )}

                {!modelConnected && (
                  <button
                    className="ob-skip-link"
                    onClick={handleSkipLater}
                    type="button"
                  >
                    Hacerlo luego — ir al chat
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    )
  }

  // ── Step 2: Your team (optional) ──────────────────────────────────────────

  if (step === 2) {
    return (
      <div className="ob-shell" role="main">
        <div className="ob-card">
          <Stepper current={2} modelConnected={modelConnected} />

          <div className="ob-step-content">
            <h1 className="ob-step__title" ref={headingRef} tabIndex={-1}>
              Tu equipo ya está listo
            </h1>
            <p className="ob-step__desc">
              Lumen incluye especialistas preconfigurados que trabajan juntos en tus tareas.
              Puedes usarlos tal cual o crear los tuyos en cualquier momento.
            </p>

            <div className="ob-team-cards">
              <div className="ob-team-card">
                <div className="ob-team-card__icon" aria-hidden="true">🔍</div>
                <div className="ob-team-card__body">
                  <div className="ob-team-card__name">Investigador</div>
                  <div className="ob-team-card__role">Busca, analiza y resume información</div>
                </div>
              </div>
              <div className="ob-team-card">
                <div className="ob-team-card__icon" aria-hidden="true">✍️</div>
                <div className="ob-team-card__body">
                  <div className="ob-team-card__name">Redactor</div>
                  <div className="ob-team-card__role">Redacta emails, informes y contenido</div>
                </div>
              </div>
              <div className="ob-team-card">
                <div className="ob-team-card__icon" aria-hidden="true">📋</div>
                <div className="ob-team-card__body">
                  <div className="ob-team-card__name">Planificador</div>
                  <div className="ob-team-card__role">Organiza proyectos y tareas</div>
                </div>
              </div>
            </div>

            <div className="ob-step__actions">
              <button
                className="cv-btn cv-btn--primary"
                onClick={() => setStep(3)}
                type="button"
              >
                Usar el equipo que viene
              </button>
              <button
                className="cv-btn cv-btn--ghost"
                onClick={() => navigate('/agentes')}
                type="button"
              >
                Ver todos los agentes
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── Step 3: Finish ────────────────────────────────────────────────────────

  return (
    <div className="ob-shell" role="main">
      <div className="ob-card ob-card--wide ob-finish">
        <div className="ob-finish__check" aria-hidden="true">✓</div>
        <h1 className="ob-finish__title" ref={headingRef} tabIndex={-1}>
          Todo listo
        </h1>
        <p className="ob-finish__subtitle">
          Pídele a Lumen lo que necesites. Puede buscar en la web, usar tus archivos
          y conectar apps — lo configuras cuando quieras.
        </p>

        <div className="ob-suggestions">
          <p className="ob-suggestions__label">Prueba con algo de esto:</p>
          <div className="ob-suggestions__grid">
            {STARTER_PROMPTS.map(prompt => (
              <button
                key={prompt}
                className="ob-suggestion-btn"
                onClick={() => handleSuggestion(prompt)}
                type="button"
              >
                {prompt}
              </button>
            ))}
          </div>
        </div>

        <button
          className="cv-btn cv-btn--primary ob-finish__cta"
          onClick={handleOpenChat}
          type="button"
        >
          Abrir el chat
        </button>
      </div>
    </div>
  )
}
