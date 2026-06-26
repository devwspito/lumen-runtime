/**
 * DashboardView — Tablero de mando del sistema.
 *
 * Fila héroe: gasto · actividad · agentes activos · aprobaciones pendientes.
 * Salud: estado del daemon/modelo.
 * Por empleado: tabla live con estado y coste de hoy, filas pinchables.
 * Próximas tareas programadas (top 5), pinchables.
 * Mini serie temporal de gasto (últimos 7 días).
 *
 * Lenguaje de negocio a lo largo. CERO jerga técnica/IA.
 * Todos los accesos a arrays protegidos con `?? []`.
 */

import { useCallback, useEffect, useReducer } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import {
  getUsageSummary,
  getUsageTimeseries,
  listPendingApprovals,
  getRuntimeStatus,
  listConfiguredTasks,
  getAgentStats,
} from '../api/client'
import type {
  UsageSummary,
  UsageTimeseries,
  PendingApproval,
  RuntimeStatus,
  ConfiguredTask,
  AgentStatsResponse,
} from '../api/types'
import { PageHeader } from '../components/ui/PageHeader'
import { Button } from '../components/ui/Button'
import { Stagger, StaggerItem, FadeIn } from '../components/ui/motion'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatUSD(v: number): string {
  if (v >= 1) return `$${v.toFixed(2)}`
  if (v > 0) return `$${v.toFixed(4)}`
  return '$0.00'
}

function formatNumber(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return String(v)
}

function formatDay(day: string): string {
  try {
    return new Date(day + 'T00:00:00').toLocaleDateString('es-ES', {
      day: 'numeric',
      month: 'short',
    })
  } catch {
    return day
  }
}

function taskLabel(t: ConfiguredTask): string {
  return t.label ?? t.title ?? t.name ?? 'Tarea programada'
}

function taskNextRun(t: ConfiguredTask): string {
  const iso = t.next_run_at
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('es-ES', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function daemonStateLabel(state: string): { label: string; ok: boolean } {
  if (state === 'idle') return { label: 'En espera', ok: true }
  if (state === 'busy' || state === 'running') return { label: 'Activo', ok: true }
  if (state === 'error') return { label: 'Con incidencia', ok: false }
  if (state === 'unknown') return { label: 'Sin conexión', ok: false }
  return { label: state, ok: true }
}

// ── State machine ─────────────────────────────────────────────────────────────

interface DashboardData {
  summary: UsageSummary
  timeseries: UsageTimeseries
  approvals: PendingApproval[]
  runtimeStatus: RuntimeStatus
  tasks: ConfiguredTask[]
  agentStats: AgentStatsResponse
}

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; data: DashboardData }

type Action =
  | { type: 'LOADED'; data: DashboardData }
  | { type: 'FAILED'; message: string }
  | { type: 'RELOAD' }

function reducer(_s: State, action: Action): State {
  switch (action.type) {
    case 'LOADED': return { status: 'success', data: action.data }
    case 'FAILED': return { status: 'error', message: action.message }
    case 'RELOAD': return { status: 'loading' }
  }
}

// ── Skeleton pieces ───────────────────────────────────────────────────────────

function SkeletonCard({ height = 88 }: { height?: number }) {
  return (
    <div
      className="cv-skeleton"
      style={{ height, borderRadius: 'var(--r-md)' }}
      aria-hidden="true"
    />
  )
}

// ── Hero stat card ─────────────────────────────────────────────────────────────

interface HeroCardProps {
  label: string
  value: string
  suffix?: string
  highlight?: boolean
  alert?: boolean
  onClick?: () => void
}

function HeroCard({ label, value, suffix, highlight, alert, onClick }: HeroCardProps) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={onClick ? 'usage-agent-row' : undefined}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sp-2)',
        flex: 1,
        minWidth: 0,
        padding: 'var(--sp-5)',
        background: highlight
          ? 'linear-gradient(135deg, color-mix(in srgb, var(--accent) 14%, var(--card)) 0%, var(--card) 100%)'
          : 'var(--card)',
        border: highlight
          ? '1px solid color-mix(in srgb, var(--accent) 30%, var(--line))'
          : alert
            ? '1px solid color-mix(in srgb, var(--warn) 40%, var(--line))'
            : '1px solid var(--line)',
        borderRadius: 'var(--r-md)',
        textAlign: 'left',
        cursor: onClick ? 'pointer' : 'default',
      }}
      aria-label={onClick ? `${label}: ${value}${suffix ? ' ' + suffix : ''}. Ver detalle.` : undefined}
    >
      <span
        style={{
          fontSize: 'var(--text-label)',
          color: 'var(--ink3)',
          fontWeight: 500,
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontSize: 'var(--text-title)',
          fontWeight: 650,
          color: alert ? 'var(--warn)' : 'var(--ink)',
          letterSpacing: '-0.03em',
          lineHeight: 1.2,
        }}
      >
        {value}
        {suffix && (
          <span
            style={{
              fontSize: 'var(--text-label)',
              fontWeight: 400,
              color: 'var(--ink3)',
              marginLeft: 4,
            }}
          >
            {suffix}
          </span>
        )}
      </span>
    </Tag>
  )
}

// ── Section title ──────────────────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2
      style={{
        fontSize: 'var(--text-label)',
        fontWeight: 650,
        color: 'var(--ink3)',
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        marginBottom: 'var(--sp-3)',
      }}
    >
      {children}
    </h2>
  )
}

// ── Chart tooltip ─────────────────────────────────────────────────────────────

interface ChartTooltipProps {
  active?: boolean
  payload?: Array<{ value: number }>
  label?: string
}

function ChartTooltip({ active, payload, label }: ChartTooltipProps) {
  if (!active || !(payload ?? []).length) return null
  const value = (payload ?? [])[0]?.value ?? 0
  return (
    <div
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--line2)',
        borderRadius: 'var(--r-sm)',
        padding: 'var(--sp-2) var(--sp-3)',
        fontSize: 'var(--text-label)',
        color: 'var(--ink)',
        boxShadow: 'var(--shadow-floating)',
      }}
    >
      <div style={{ color: 'var(--ink3)', marginBottom: 2 }}>{label ? formatDay(label) : ''}</div>
      <div style={{ fontWeight: 600 }}>{formatUSD(value)}</div>
    </div>
  )
}

// ── Estado del sistema ────────────────────────────────────────────────────────

interface SystemHealthProps {
  runtimeStatus: RuntimeStatus
  agentStats: AgentStatsResponse
}

function SystemHealth({ runtimeStatus, agentStats }: SystemHealthProps) {
  const { label, ok } = daemonStateLabel(runtimeStatus.state)
  const agents = agentStats.agents ?? []
  const working = agents.filter(a => a.state === 'working').length
  const withIncident = agents.filter(a => a.health && a.health !== 'ok').length

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 'var(--sp-3)',
      }}
    >
      <div
        style={{
          background: 'var(--card)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-md)',
          padding: 'var(--sp-4)',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--sp-3)',
          flex: 1,
          minWidth: 200,
        }}
      >
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: ok ? 'var(--ok)' : 'var(--danger)',
            flexShrink: 0,
          }}
          aria-hidden="true"
        />
        <div>
          <div style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Sistema
          </div>
          <div style={{ fontSize: 'var(--text-body)', fontWeight: 600, color: 'var(--ink)', marginTop: 2 }}>
            {label}
          </div>
        </div>
      </div>

      <div
        style={{
          background: 'var(--card)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-md)',
          padding: 'var(--sp-4)',
          flex: 1,
          minWidth: 200,
        }}
      >
        <div style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Empleados activos ahora
        </div>
        <div style={{ fontSize: 'var(--text-subtitle)', fontWeight: 650, color: 'var(--ink)', marginTop: 4, letterSpacing: '-0.02em' }}>
          {working > 0 ? working : '—'}
        </div>
        {working > 0 && (
          <div style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', marginTop: 2 }}>
            de {agents.length} en la plataforma
          </div>
        )}
      </div>

      {withIncident > 0 && (
        <div
          style={{
            background: 'color-mix(in srgb, var(--warn) 10%, var(--card))',
            border: '1px solid color-mix(in srgb, var(--warn) 30%, var(--line))',
            borderRadius: 'var(--r-md)',
            padding: 'var(--sp-4)',
            flex: 1,
            minWidth: 200,
          }}
          role="alert"
        >
          <div style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            Incidencias
          </div>
          <div style={{ fontSize: 'var(--text-subtitle)', fontWeight: 650, color: 'var(--warn)', marginTop: 4, letterSpacing: '-0.02em' }}>
            {withIncident} empleado{withIncident > 1 ? 's' : ''} con alerta
          </div>
        </div>
      )}
    </div>
  )
}

// ── Tabla de empleados ─────────────────────────────────────────────────────────

interface EmployeeTableProps {
  agentStats: AgentStatsResponse
  onRowClick: () => void
}

function EmployeeTable({ agentStats, onRowClick }: EmployeeTableProps) {
  const agents = (agentStats.agents ?? []).slice().sort((a, b) => b.today.cost_usd - a.today.cost_usd)

  if (!agentStats.available || agents.length === 0) {
    return (
      <p className="cv-empty">
        Sin datos de empleados disponibles aún.
      </p>
    )
  }

  return (
    <div
      style={{ overflowX: 'auto' }}
      role="region"
      aria-label="Tabla de empleados"
    >
      <table
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: 'var(--text-body)',
        }}
      >
        <thead>
          <tr>
            {['Empleado', 'Estado', 'Tareas hoy', 'Gasto hoy'].map(h => (
              <th
                key={h}
                scope="col"
                style={{
                  padding: 'var(--sp-2) var(--sp-3)',
                  textAlign: h === 'Tareas hoy' || h === 'Gasto hoy' ? 'right' : 'left',
                  fontSize: 'var(--text-caption)',
                  fontWeight: 600,
                  color: 'var(--ink4)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  borderBottom: '1px solid var(--line)',
                  whiteSpace: 'nowrap',
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {agents.map(agent => (
            <tr
              key={agent.agent_id}
              style={{ cursor: 'pointer' }}
              onClick={onRowClick}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick() } }}
              tabIndex={0}
              role="button"
              aria-label={`Ver detalle de ${agent.name}`}
              className="dashboard-agent-row"
            >
              <td
                style={{
                  padding: 'var(--sp-3)',
                  borderBottom: '1px solid var(--line)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
                  <div
                    style={{
                      width: 28,
                      height: 28,
                      borderRadius: '50%',
                      background: agent.color ?? 'var(--accent)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 'var(--text-label)',
                      fontWeight: 700,
                      color: '#fff',
                      flexShrink: 0,
                    }}
                    aria-hidden="true"
                  >
                    {agent.name.charAt(0).toUpperCase()}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div
                      style={{
                        fontWeight: 600,
                        color: 'var(--ink)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {agent.name}
                    </div>
                    {agent.department && (
                      <div
                        style={{
                          fontSize: 'var(--text-caption)',
                          color: 'var(--ink4)',
                        }}
                      >
                        {agent.department}
                      </div>
                    )}
                  </div>
                </div>
              </td>
              <td
                style={{
                  padding: 'var(--sp-3)',
                  borderBottom: '1px solid var(--line)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
                  <span
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: agent.state === 'working' ? 'var(--warn)' : 'var(--ok)',
                      flexShrink: 0,
                    }}
                    aria-hidden="true"
                  />
                  <span style={{ color: 'var(--ink2)', fontSize: 'var(--text-label)' }}>
                    {agent.state === 'working' ? 'Trabajando' : 'En espera'}
                  </span>
                </div>
              </td>
              <td
                style={{
                  padding: 'var(--sp-3)',
                  borderBottom: '1px solid var(--line)',
                  textAlign: 'right',
                  fontVariantNumeric: 'tabular-nums',
                  color: 'var(--ink2)',
                }}
              >
                {formatNumber(agent.today.tasks)}
              </td>
              <td
                style={{
                  padding: 'var(--sp-3)',
                  borderBottom: '1px solid var(--line)',
                  textAlign: 'right',
                  fontVariantNumeric: 'tabular-nums',
                  fontWeight: 600,
                  color: agent.today.cost_usd > 0 ? 'var(--ink)' : 'var(--ink4)',
                }}
              >
                {agent.today.cost_usd > 0 ? formatUSD(agent.today.cost_usd) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Próximas tareas ────────────────────────────────────────────────────────────

interface UpcomingTasksProps {
  tasks: ConfiguredTask[]
  onRowClick: () => void
}

function UpcomingTasks({ tasks, onRowClick }: UpcomingTasksProps) {
  const enabled = (tasks ?? []).filter(t => t.enabled !== false).slice(0, 5)

  if (enabled.length === 0) {
    return (
      <p className="cv-empty">Sin tareas programadas activas.</p>
    )
  }

  return (
    <ul role="list" style={{ display: 'flex', flexDirection: 'column', gap: 4, listStyle: 'none' }}>
      {enabled.map((task, i) => {
        const id = task.trigger_id ?? task.task_id ?? task.id ?? String(i)
        return (
          <li key={id}>
            <button
              type="button"
              onClick={onRowClick}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--sp-3)',
                width: '100%',
                padding: 'var(--sp-3) var(--sp-4)',
                background: 'var(--card)',
                border: '1px solid var(--line)',
                borderRadius: 'var(--r-sm)',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'border-color var(--ease-hover), background var(--ease-hover)',
              }}
              className="usage-agent-row"
              aria-label={`Ver tarea: ${taskLabel(task)}`}
            >
              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: 'var(--ok)',
                  flexShrink: 0,
                }}
                aria-hidden="true"
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 'var(--text-body)',
                    fontWeight: 600,
                    color: 'var(--ink)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {taskLabel(task)}
                </div>
                {(task.cron ?? task.recurrence_human) && (
                  <div style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', marginTop: 1 }}>
                    {task.recurrence_human ?? task.cron}
                  </div>
                )}
              </div>
              <span
                style={{
                  fontSize: 'var(--text-caption)',
                  color: 'var(--ink3)',
                  flexShrink: 0,
                  whiteSpace: 'nowrap',
                }}
              >
                {taskNextRun(task)}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

// ── Dashboard view ─────────────────────────────────────────────────────────────

export default function DashboardView() {
  const navigate = useNavigate()
  const [state, dispatch] = useReducer(reducer, { status: 'loading' })

  const load = useCallback(() => {
    dispatch({ type: 'RELOAD' })
    Promise.all([
      getUsageSummary('7d'),
      getUsageTimeseries('7d', 'cost'),
      listPendingApprovals(),
      getRuntimeStatus(),
      listConfiguredTasks(),
      getAgentStats(),
    ]).then(([summary, timeseries, approvals, runtimeStatus, tasksResp, agentStats]) => {
      dispatch({
        type: 'LOADED',
        data: {
          summary,
          timeseries,
          approvals: approvals ?? [],
          runtimeStatus,
          tasks: tasksResp.tasks ?? [],
          agentStats,
        },
      })
    }).catch((err: unknown) => {
      dispatch({
        type: 'FAILED',
        message: err instanceof Error ? err.message : 'No se pudo cargar el tablero.',
      })
    })
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <>
      <PageHeader
        title="Tablero"
        subtitle="Resumen de actividad, gasto y estado del equipo."
        actions={
          <Button variant="secondary" onClick={load} style={{ fontSize: 'var(--text-label)' }}>
            Actualizar
          </Button>
        }
      />

      <div className="view-body cv-view-body">

        {/* ── Loading ── */}
        {state.status === 'loading' && (
          <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-8)' }}>
            <StaggerItem>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 'var(--sp-3)' }}>
                {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
              </div>
            </StaggerItem>
            <StaggerItem><SkeletonCard height={80} /></StaggerItem>
            <StaggerItem><SkeletonCard height={200} /></StaggerItem>
            <StaggerItem>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 'var(--sp-6)' }}>
                <SkeletonCard height={220} />
                <SkeletonCard height={160} />
              </div>
            </StaggerItem>
          </Stagger>
        )}

        {/* ── Error ── */}
        {state.status === 'error' && (
          <FadeIn>
            <div className="state-container" role="alert">
              <p className="state-error">{state.message}</p>
              <Button variant="secondary" onClick={load}>Reintentar</Button>
            </div>
          </FadeIn>
        )}

        {/* ── Success ── */}
        {state.status === 'success' && (() => {
          const { summary, timeseries, approvals, runtimeStatus, tasks, agentStats } = state.data

          const pendingCount = (approvals ?? []).length
          const agentList = agentStats.agents ?? []
          const activeAgents = agentList.filter(a => a.state === 'working').length
          const totalAgents = agentList.length

          const chartPoints = (timeseries.points ?? []).map(p => ({
            day: p.day,
            value: p.cost_usd,
          }))

          return (
            <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-8)' }}>

              {/* ── 1. Fila héroe ── */}
              <StaggerItem>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                    gap: 'var(--sp-3)',
                  }}
                  role="list"
                  aria-label="Métricas principales"
                >
                  <HeroCard
                    label="Gasto (7 días)"
                    value={formatUSD(summary.total_cost_usd)}
                    highlight
                    onClick={() => navigate('/coste')}
                  />
                  <HeroCard
                    label="Actividad (7 días)"
                    value={formatNumber(summary.cycles)}
                    suffix="acciones"
                    onClick={() => navigate('/coste')}
                  />
                  <HeroCard
                    label="Empleados activos"
                    value={activeAgents > 0 ? String(activeAgents) : '—'}
                    suffix={totalAgents > 0 ? `de ${totalAgents}` : undefined}
                    onClick={() => navigate('/agentes')}
                  />
                  <HeroCard
                    label="Pendientes de aprobar"
                    value={pendingCount > 0 ? String(pendingCount) : 'Ninguna'}
                    alert={pendingCount > 0}
                    onClick={() => navigate('/seguridad')}
                  />
                </div>
              </StaggerItem>

              {/* ── 2. Estado del sistema ── */}
              <StaggerItem>
                <section aria-label="Estado del sistema">
                  <SectionTitle>Estado del sistema</SectionTitle>
                  <SystemHealth runtimeStatus={runtimeStatus} agentStats={agentStats} />
                </section>
              </StaggerItem>

              {/* ── 3. Mini serie temporal ── */}
              {chartPoints.length > 0 && (
                <StaggerItem>
                  <section aria-label="Gasto reciente">
                    <SectionTitle>Gasto últimos 7 días</SectionTitle>
                    <div
                      style={{
                        background: 'var(--card)',
                        border: '1px solid var(--line)',
                        borderRadius: 'var(--r-md)',
                        padding: 'var(--sp-5)',
                      }}
                    >
                      <ResponsiveContainer width="100%" height={140}>
                        <AreaChart data={chartPoints} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                          <defs>
                            <linearGradient id="dbGradient" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.25} />
                              <stop offset="95%" stopColor="var(--accent)" stopOpacity={0.02} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" vertical={false} />
                          <XAxis
                            dataKey="day"
                            tickFormatter={formatDay}
                            tick={{ fontSize: 11, fill: 'var(--ink4)' }}
                            axisLine={false}
                            tickLine={false}
                            interval="preserveStartEnd"
                          />
                          <YAxis
                            tickFormatter={v => formatUSD(v as number)}
                            tick={{ fontSize: 11, fill: 'var(--ink4)' }}
                            axisLine={false}
                            tickLine={false}
                            width={56}
                          />
                          <Tooltip
                            content={<ChartTooltip />}
                            cursor={{ stroke: 'var(--line2)', strokeWidth: 1 }}
                          />
                          <Area
                            type="monotone"
                            dataKey="value"
                            stroke="var(--accent)"
                            strokeWidth={2}
                            fill="url(#dbGradient)"
                            dot={false}
                            activeDot={{ r: 4, fill: 'var(--accent)', strokeWidth: 0 }}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  </section>
                </StaggerItem>
              )}

              {/* ── 4. Dos columnas: empleados + próximas tareas ── */}
              <StaggerItem>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
                    gap: 'var(--sp-6)',
                    alignItems: 'start',
                  }}
                >
                  <section aria-label="Estado de empleados">
                    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 'var(--sp-3)' }}>
                      <SectionTitle>Por empleado</SectionTitle>
                      <button
                        type="button"
                        className="cv-btn cv-btn--ghost cv-btn--sm"
                        onClick={() => navigate('/agentes')}
                        style={{ fontSize: 'var(--text-caption)' }}
                      >
                        Ver todos
                      </button>
                    </div>
                    <div
                      style={{
                        background: 'var(--card)',
                        border: '1px solid var(--line)',
                        borderRadius: 'var(--r-md)',
                        overflow: 'hidden',
                      }}
                    >
                      <EmployeeTable
                        agentStats={agentStats}
                        onRowClick={() => navigate('/agentes')}
                      />
                    </div>
                  </section>

                  <section aria-label="Próximas tareas programadas">
                    <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 'var(--sp-3)' }}>
                      <SectionTitle>Próximas tareas</SectionTitle>
                      <button
                        type="button"
                        className="cv-btn cv-btn--ghost cv-btn--sm"
                        onClick={() => navigate('/programadas')}
                        style={{ fontSize: 'var(--text-caption)' }}
                      >
                        Ver todas
                      </button>
                    </div>
                    <UpcomingTasks
                      tasks={tasks}
                      onRowClick={() => navigate('/programadas')}
                    />
                  </section>
                </div>
              </StaggerItem>

            </Stagger>
          )
        })()}

      </div>
    </>
  )
}
