/**
 * ChatView — streaming chat with the Lumen agent.
 *
 * Layout: topbar / scrollable message list / composer (matches the vanilla
 * #center column). Sidebar recents are shown in the parent Layout's sidebar slot
 * via context (not done here — the sidebar is a separate concern). The chat
 * logic lives in useChat; this component is purely presentational.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ChangeEvent,
} from 'react'
import { useNavigate } from 'react-router-dom'
import { useChat } from '../hooks/useChat'
import type { ChatMessage, ToolStep } from '../hooks/useChat'
import { listProviders } from '../api/client'
import type { Provider } from '../api/types'
import { ChatBridgeContext } from '../components/Layout'

// ── i18n strings (ES, matching vanilla i18n.js) ───────────────────────────────

const STRINGS = {
  welcomeTitle:  'Hola, soy Lumen',
  welcomeSubtitle: 'Tu agente de trabajo personal. Dime en qué puedo ayudarte hoy.',
  suggest1: 'Investiga los mejores CRMs para una startup B2B',
  suggest2: 'Redacta un email de propuesta comercial',
  suggest3: 'Organiza mis tareas de esta semana en un plan de acción',
  suggest4: 'Analiza este documento y extrae los puntos clave',
  placeholder: 'Escribe a Lumen…',
  send: 'Enviar',
  stop: 'Detener',
  disclaimer: 'Lumen es IA y puede cometer errores. Verifica las respuestas importantes.',
}

const SUGGESTIONS = [
  STRINGS.suggest1,
  STRINGS.suggest2,
  STRINGS.suggest3,
  STRINGS.suggest4,
]

// ── Welcome screen ────────────────────────────────────────────────────────────

interface WelcomeProps {
  onSuggestion(text: string): void
}

function Welcome({ onSuggestion }: WelcomeProps) {
  return (
    <div className="chat-welcome" role="main">
      <div className="welcome-mark" aria-hidden="true">L</div>
      <h1 className="welcome-title">{STRINGS.welcomeTitle}</h1>
      <p className="welcome-subtitle">{STRINGS.welcomeSubtitle}</p>
      <div className="welcome-suggestions" role="list" aria-label="Sugerencias">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            className="suggestion-pill"
            role="listitem"
            onClick={() => onSuggestion(s)}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Tool summary block ────────────────────────────────────────────────────────

interface ToolSummaryProps {
  steps: ToolStep[]
  isStreaming: boolean
}

function ToolSummary({ steps, isStreaming }: ToolSummaryProps) {
  if (steps.length === 0) return null
  const count = steps.length
  const last = steps[steps.length - 1]
  const label = isStreaming
    ? `${last.label}${last.target ? ` — ${last.target.slice(0, 48)}` : ''}`
    : `Usó ${count} herramienta${count > 1 ? 's' : ''}`

  return (
    <details className="tool-summary-group">
      <summary className="tool-summary-group__summary">
        <span className="tool-summary-group__label">{label}</span>
        {!isStreaming && (
          <span className="tool-summary-group__count" aria-label={`${count} herramientas`}>
            {count}
          </span>
        )}
        <span className="tool-summary-group__chevron" aria-hidden="true">
          <ChevronIcon />
        </span>
      </summary>
      <div className="tool-summary-group__body">
        {steps.map((step, i) => (
          <div key={i} className="tool-step-item">
            <span className="tool-step-item__label">{step.label}</span>
            {step.target && (
              <span className="tool-step-item__target">{step.target}</span>
            )}
          </div>
        ))}
      </div>
    </details>
  )
}

// ── Thinking block ────────────────────────────────────────────────────────────

interface ThinkingBlockProps {
  text: string
  done: boolean
}

function ThinkingBlock({ text, done }: ThinkingBlockProps) {
  if (!text) return null
  return (
    <details className="thinking-block">
      <summary className="thinking-block__summary">
        <span className="thinking-block__label">
          {done ? 'Proceso de pensamiento' : 'Pensando…'}
        </span>
        <span className="thinking-block__chevron" aria-hidden="true">
          <ChevronIcon />
        </span>
      </summary>
      <div className="thinking-block__body">{text}</div>
    </details>
  )
}

// ── Message bubbles ───────────────────────────────────────────────────────────

interface UserMessageProps {
  text: string
}

function UserMessage({ text }: UserMessageProps) {
  return (
    <div className="message message--user" role="article" aria-label="Tu mensaje">
      <div className="message__bubble">{text}</div>
    </div>
  )
}

interface AssistantMessageProps {
  message: Extract<ChatMessage, { type: 'assistant' }>
}

function AssistantMessage({ message }: AssistantMessageProps) {
  const { thinkingText, thinkingDone, toolSteps, activityText, renderedHtml, isStreaming } = message

  return (
    <div className="message message--agent" role="article" aria-label="Respuesta de Lumen">
      <ThinkingBlock text={thinkingText} done={thinkingDone} />
      <ToolSummary steps={toolSteps} isStreaming={isStreaming} />

      {/* Live activity excerpt while streaming */}
      {isStreaming && activityText && (
        <div className="agent-activity" aria-live="polite" aria-atomic="false">
          {lastLine(activityText)}
        </div>
      )}

      {/* Final rendered markdown (shown after stream completes) */}
      {!isStreaming && renderedHtml && (
        <div
          className="agent-prose"
          /* Safe: renderedHtml is produced by DOMPurify.sanitize — see lib/markdown.ts */
          dangerouslySetInnerHTML={{ __html: renderedHtml }}
        />
      )}

      {/* Streaming cursor */}
      {isStreaming && !activityText && (
        <div className="agent-activity" aria-live="polite">
          <span className="spin" aria-hidden="true">⟳</span>
        </div>
      )}
    </div>
  )
}

function lastLine(text: string): string {
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean)
  return lines.length ? `· ${lines[lines.length - 1]}` : ''
}

// ── Status bar ────────────────────────────────────────────────────────────────

interface StatusBarProps {
  phase: string
  text?: string
}

function StatusBar({ phase, text }: StatusBarProps) {
  if (phase === 'idle') return null
  const isError = phase === 'error'

  return (
    <div
      className={`chat-status${isError ? ' chat-status--error' : ''}`}
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
    >
      {!isError && <SpinnerIcon />}
      <span>{text}</span>
    </div>
  )
}

// ── Model picker ──────────────────────────────────────────────────────────────

function useActiveProvider() {
  const [provider, setProvider] = useState<Provider | null>(null)

  useEffect(() => {
    listProviders()
      .then(data => {
        const arr = Array.isArray(data) ? data : []
        setProvider(arr.find(p => p.is_active) ?? arr[0] ?? null)
      })
      .catch(() => setProvider(null))
  }, [])

  return provider
}

function ModelPicker() {
  const navigate = useNavigate()
  const provider = useActiveProvider()

  const label = provider
    ? (provider.default_model ?? provider.alias ?? provider.name ?? 'Modelo activo')
    : 'Sin modelo configurado'

  return (
    <button
      className="composer-model-picker"
      onClick={() => navigate('/proveedores')}
      title={provider ? `Proveedor: ${provider.alias ?? provider.name}` : 'Configura un modelo en Proveedores'}
      type="button"
      aria-label={provider ? `Modelo activo: ${label}. Ir a Proveedores` : 'Sin modelo. Ir a Proveedores'}
    >
      <span className="composer-model-picker__label">{label}</span>
      <ChevronIcon />
    </button>
  )
}

// ── Composer ──────────────────────────────────────────────────────────────────

interface ComposerProps {
  disabled: boolean
  isStreaming: boolean
  onSend(text: string): void
  onStop(): void
  value: string
  onChange(v: string): void
}

function Composer({ disabled, isStreaming, onSend, onStop, value, onChange }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-grow textarea
  useLayoutEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [value])

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!disabled && value.trim()) onSend(value)
    }
  }

  function handleChange(e: ChangeEvent<HTMLTextAreaElement>) {
    onChange(e.target.value)
  }

  return (
    <div className="composer-wrap">
      <div className="composer-box">
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          placeholder={STRINGS.placeholder}
          aria-label="Escribe un mensaje para Lumen"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={1}
        />
        <div className="composer-toolbar">
          <ModelPicker />
          <div className="composer-toolbar-right">
            {isStreaming ? (
              <button
                className="composer-stop"
                onClick={onStop}
                aria-label="Detener generación"
                type="button"
              >
                {STRINGS.stop}
              </button>
            ) : (
              <button
                className="composer-send"
                onClick={() => { if (value.trim()) onSend(value) }}
                disabled={disabled || !value.trim()}
                aria-label="Enviar mensaje (Enter)"
                type="button"
              >
                {STRINGS.send}
              </button>
            )}
          </div>
        </div>
      </div>
      <p className="composer-footer">{STRINGS.disclaimer}</p>
    </div>
  )
}

// ── Micro icons ───────────────────────────────────────────────────────────────

function ChevronIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M4 3l4 3-4 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function SpinnerIcon() {
  return (
    <svg className="spin" width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.5" strokeDasharray="14 8" />
    </svg>
  )
}

// ── ChatView ──────────────────────────────────────────────────────────────────

export default function ChatView() {
  const { messages, status, sendMessage, stopStream, convId, loadConversation } = useChat()
  const [composerText, setComposerText] = useState('')
  const bodyRef = useRef<HTMLDivElement>(null)
  const userScrolledRef = useRef(false)
  const pinRef = useRef(true)

  const isStreaming = status.phase === 'streaming' || status.phase === 'sending'
  const showWelcome = messages.length === 0

  // Scroll pinning — matches vanilla scrollToBottom logic
  useEffect(() => {
    const el = bodyRef.current
    if (!el) return
    function onScroll() {
      const nearBottom = el!.scrollTop + el!.clientHeight >= el!.scrollHeight - 80
      pinRef.current = nearBottom
      userScrolledRef.current = !nearBottom
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useLayoutEffect(() => {
    const el = bodyRef.current
    if (!el) return
    if (!userScrolledRef.current || pinRef.current) {
      el.scrollTop = el.scrollHeight
    }
  })

  const handleSend = useCallback((text: string) => {
    userScrolledRef.current = false
    pinRef.current = true
    setComposerText('')
    sendMessage(text)
  }, [sendMessage])

  const handleSuggestion = useCallback((text: string) => {
    handleSend(text)
  }, [handleSend])

  const statusText = status.phase === 'streaming' ? status.statusText
    : status.phase === 'sending' ? 'Enviando…'
    : status.phase === 'error' ? status.message
    : undefined

  return (
    <ChatBridgeContext.Provider value={{ activeConvId: convId, loadConversation }}>
      <div className="chat-view">
        {/* Topbar */}
        <div className="chat-topbar">
          <span className="chat-topbar-title">
            {showWelcome ? 'Nueva conversación' : 'Chat'}
          </span>
        </div>

        {/* Messages */}
        <div
          className="chat-body"
          ref={bodyRef}
          aria-live="polite"
          aria-label="Mensajes del chat"
        >
          {showWelcome ? (
            <Welcome onSuggestion={handleSuggestion} />
          ) : (
            messages.map((msg) =>
              msg.type === 'user' ? (
                <UserMessage key={msg.id} text={msg.text} />
              ) : (
                <AssistantMessage key={msg.id} message={msg} />
              ),
            )
          )}
        </div>

        {/* Status bar */}
        <StatusBar phase={status.phase} text={statusText} />

        {/* Composer */}
        <Composer
          disabled={status.phase === 'sending'}
          isStreaming={isStreaming}
          onSend={handleSend}
          onStop={stopStream}
          value={composerText}
          onChange={setComposerText}
        />
      </div>
    </ChatBridgeContext.Provider>
  )
}
