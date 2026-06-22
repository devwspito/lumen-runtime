import { useEffect, useReducer } from 'react'
import { listAgents, getActiveAgent, ApiError } from '../api/client'
import type { Agent } from '../api/types'

// Discriminated union — impossible to be in loading+error simultaneously.
type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; agents: Agent[]; activeAgentId: string }

type Action =
  | { type: 'LOADED'; agents: Agent[]; activeAgentId: string }
  | { type: 'FAILED'; message: string }

function reducer(_state: State, action: Action): State {
  switch (action.type) {
    case 'LOADED':
      return { status: 'success', agents: action.agents, activeAgentId: action.activeAgentId }
    case 'FAILED':
      return { status: 'error', message: action.message }
  }
}

export default function AgentsView() {
  const [state, dispatch] = useReducer(reducer, { status: 'loading' })

  useEffect(() => {
    let cancelled = false

    Promise.all([listAgents(), getActiveAgent()])
      .then(([agents, active]) => {
        if (!cancelled) {
          dispatch({
            type: 'LOADED',
            agents,
            activeAgentId: active.active_agent_id,
          })
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message =
            err instanceof ApiError
              ? err.message
              : 'No se pudieron cargar los agentes.'
          dispatch({ type: 'FAILED', message })
        }
      })

    return () => { cancelled = true }
  }, [])

  return (
    <>
      <header className="view-header">
        <h1 className="view-title">Agentes</h1>
        <p className="view-subtitle">Tus asistentes de IA configurados en Lumen</p>
      </header>

      <div className="view-body">
        {state.status === 'loading' && (
          <div className="state-container" aria-live="polite" aria-busy="true">
            <p className="state-label">Cargando agentes…</p>
          </div>
        )}

        {state.status === 'error' && (
          <div className="state-container" role="alert">
            <p className="state-error">{state.message}</p>
          </div>
        )}

        {state.status === 'success' && state.agents.length === 0 && (
          <div className="state-container">
            <p className="state-label">No hay agentes configurados.</p>
          </div>
        )}

        {state.status === 'success' && state.agents.length > 0 && (
          <ul className="agent-grid" role="list" aria-label="Lista de agentes">
            {state.agents.map((agent) => (
              <li key={agent.id}>
                <AgentCard
                  agent={agent}
                  isActive={agent.id === state.activeAgentId}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

interface AgentCardProps {
  agent: Agent
  isActive: boolean
}

function AgentCard({ agent, isActive }: AgentCardProps) {
  const initials = agent.name.charAt(0).toUpperCase()

  return (
    <article
      className="agent-card"
      aria-label={`Agente: ${agent.name}${isActive ? ', activo' : ''}`}
    >
      <div className="agent-card-header">
        <div
          className="agent-avatar"
          style={{ background: agent.color }}
          aria-hidden="true"
        >
          {initials}
        </div>

        <div className="agent-meta">
          <p className="agent-name">{agent.name}</p>
          {agent.role && <p className="agent-role">{agent.role}</p>}
        </div>

        {agent.is_default && (
          <span className="badge" aria-label="Agente cerebro">
            Cerebro
          </span>
        )}
      </div>

      {agent.primary_mission && (
        <p className="agent-mission">{agent.primary_mission}</p>
      )}
    </article>
  )
}
