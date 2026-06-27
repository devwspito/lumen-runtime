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
import { Badge, StatusDot } from '../components/ui/Badge'
import { Stagger, StaggerItem, FadeIn } from '../components/ui/motion'
import styles from './DashboardView.module.css'

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

// ── Skeleton — mirrors final layout ──────────────────────────────────────────

/** Inline shimmer span — avoids Skeleton's style prop limitation. */
function Sh({ w, h, extra }: { w: string; h: string; extra?: React.CSSProperties }) {
  return (
    <span
      className={styles.shimmer}
      style={{ width: w, height: h, ...extra }}
      aria-hidden="true"
    />
  )
}

function DashboardSkeleton() {
  return (
    <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>

      {/* KPI row */}
      <StaggerItem>
        <div className={styles.skeletonKpiGrid} aria-hidden="true">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className={styles.skeletonKpiCard}>
              <Sh w="72px" h="11px" />
              <Sh w="110px" h="28px" />
              <Sh w="56px" h="10px" />
            </div>
          ))}
        </div>
      </StaggerItem>

      {/* Health strip */}
      <StaggerItem>
        <Sh w="96px" h="11px" extra={{ marginBottom: 'var(--space-3)', display: 'block' }} />
        <div className={styles.skeletonHealth} aria-hidden="true">
          {[0, 1, 2].map(i => (
            <div key={i} className={styles.skeletonHealthCard}>
              <Sh w="9px" h="9px" extra={{ borderRadius: '50%', flexShrink: 0 }} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)', flex: 1 }}>
                <Sh w="60px" h="10px" />
                <Sh w="90px" h="14px" />
              </div>
            </div>
          ))}
        </div>
      </StaggerItem>

      {/* Chart */}
      <StaggerItem>
        <Sh w="96px" h="11px" extra={{ marginBottom: 'var(--space-3)', display: 'block' }} />
        <div className={styles.skeletonPanel}>
          <Sh w="100%" h="140px" extra={{ borderRadius: 'var(--radius-md)' }} />
        </div>
      </StaggerItem>

      {/* Two-column */}
      <StaggerItem>
        <div className={styles.twoCol}>
          <div>
            <Sh w="80px" h="11px" extra={{ marginBottom: 'var(--space-3)', display: 'block' }} />
            <div className={styles.skeletonPanel}>
              {[0, 1, 2, 3].map(i => (
                <div key={i} className={styles.skeletonRow}>
                  <Sh w="26px" h="26px" extra={{ borderRadius: '50%', flexShrink: 0 }} />
                  <Sh w="120px" h="13px" />
                  <Sh w="60px" h="13px" extra={{ marginLeft: 'auto' }} />
                </div>
              ))}
            </div>
          </div>
          <div>
            <Sh w="96px" h="11px" extra={{ marginBottom: 'var(--space-3)', display: 'block' }} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-1)' }}>
              {[0, 1, 2, 3].map(i => (
                <Sh key={i} w="100%" h="52px" extra={{ borderRadius: 'var(--radius-md)' }} />
              ))}
            </div>
          </div>
        </div>
      </StaggerItem>

    </Stagger>
  )
}

// ── KPI hero card ─────────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string
  value: string
  suffix?: string
  subtext?: string
  variant?: 'default' | 'highlight' | 'alert'
  onClick?: () => void
}

function KpiCard({ label, value, suffix, subtext, variant = 'default', onClick }: KpiCardProps) {
  const Tag = onClick ? 'button' : 'div'
  const cardClass = [
    styles.kpiCard,
    onClick ? styles['kpiCard--clickable'] : '',
    variant === 'highlight' ? styles['kpiCard--highlight'] : '',
    variant === 'alert' ? styles['kpiCard--alert'] : '',
  ].filter(Boolean).join(' ')

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cardClass}
      aria-label={onClick ? `${label}: ${value}${suffix ? ' ' + suffix : ''}. Ver detalle.` : undefined}
    >
      <span className={styles.kpiLabel}>{label}</span>
      <span className={variant === 'alert' ? `${styles.kpiValue} ${styles['kpiValue--alert']}` : styles.kpiValue}>
        {value}
        {suffix && <span className={styles.kpiSuffix}>{suffix}</span>}
      </span>
      {subtext && <span className={styles.kpiSubtext}>{subtext}</span>}
    </Tag>
  )
}

// ── Section header ─────────────────────────────────────────────────────────────

interface SectionHeaderProps {
  title: string
  onLinkClick?: () => void
  linkLabel?: string
}

function SectionHeader({ title, onLinkClick, linkLabel = 'Ver todos' }: SectionHeaderProps) {
  return (
    <div className={styles.sectionHeader}>
      <h2 className={styles.sectionTitle}>{title}</h2>
      {onLinkClick && (
        <button type="button" className={styles.sectionLink} onClick={onLinkClick}>
          {linkLabel}
        </button>
      )}
    </div>
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
    <div className={styles.chartTooltip}>
      <div className={styles.chartTooltipDate}>{label ? formatDay(label) : ''}</div>
      <div className={styles.chartTooltipValue}>{formatUSD(value)}</div>
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
    <div className={styles.healthStrip}>
      {/* System state */}
      <div className={styles.healthCard}>
        <span
          className={`${styles.healthDot} ${ok ? styles['healthDot--ok'] : styles['healthDot--error']}`}
          aria-hidden="true"
        />
        <div className={styles.healthMeta}>
          <span className={styles.healthMetaLabel}>Sistema</span>
          <span className={styles.healthMetaValue}>{label}</span>
        </div>
        <Badge variant={ok ? 'success' : 'danger'}>{ok ? 'Operativo' : 'Alerta'}</Badge>
      </div>

      {/* Empleados activos */}
      <div className={styles.healthCard}>
        <StatusDot state={working > 0 ? 'warning' : 'success'} />
        <div className={styles.healthMeta}>
          <span className={styles.healthMetaLabel}>Empleados activos</span>
          <span className={`${styles.healthNum}`}>{working > 0 ? working : '—'}</span>
          {agents.length > 0 && (
            <span className={styles.healthMetaSub}>de {agents.length} en la plataforma</span>
          )}
        </div>
      </div>

      {/* Incidencias (sólo si hay) */}
      {withIncident > 0 && (
        <div className={`${styles.healthCard} ${styles['healthCard--warning']}`} role="alert">
          <span className={`${styles.healthDot} ${styles['healthDot--error']}`} aria-hidden="true" />
          <div className={styles.healthMeta}>
            <span className={styles.healthMetaLabel}>Incidencias</span>
            <span className={`${styles.healthMetaValue} ${styles['healthMetaValue--warning']}`}>
              {withIncident} empleado{withIncident > 1 ? 's' : ''} con alerta
            </span>
          </div>
          <Badge variant="warning">{withIncident}</Badge>
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
      <div className={styles.emptyPanel}>
        <svg className={styles.emptyIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
        </svg>
        <span className={styles.emptyTitle}>Sin datos de empleados aún</span>
        <span className={styles.emptyDesc}>La actividad aparecerá aquí en cuanto comiencen a trabajar.</span>
      </div>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }} role="region" aria-label="Tabla de empleados">
      <table className={styles.table}>
        <thead className={styles.tableHead}>
          <tr>
            <th scope="col">Empleado</th>
            <th scope="col">Estado</th>
            <th scope="col" className={styles.alignRight}>Tareas hoy</th>
            <th scope="col" className={styles.alignRight}>Gasto hoy</th>
          </tr>
        </thead>
        <tbody>
          {agents.map(agent => (
            <tr
              key={agent.agent_id}
              className={styles.tableRow}
              onClick={onRowClick}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick() } }}
              tabIndex={0}
              role="button"
              aria-label={`Ver detalle de ${agent.name}`}
            >
              <td className={styles.tableCell}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
                  <div
                    className={styles.agentAvatar}
                    style={{ background: agent.color ?? 'var(--color-accent)' }}
                    aria-hidden="true"
                  >
                    {agent.name.charAt(0).toUpperCase()}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className={styles.agentName}>{agent.name}</div>
                    {agent.department && (
                      <div className={styles.agentDept}>{agent.department}</div>
                    )}
                  </div>
                </div>
              </td>
              <td className={styles.tableCell}>
                <div className={styles.stateBadgeRow}>
                  <span
                    className={`${styles.stateDot} ${agent.state === 'working' ? styles['stateDot--working'] : styles['stateDot--idle']}`}
                    aria-hidden="true"
                  />
                  <span className={styles.stateLabel}>
                    {agent.state === 'working' ? 'Trabajando' : 'En espera'}
                  </span>
                </div>
              </td>
              <td className={`${styles.tableCell} ${styles['tableCell--right']}`}>
                <span className={styles.numCell}>{formatNumber(agent.today.tasks)}</span>
              </td>
              <td className={`${styles.tableCell} ${styles['tableCell--right']}`}>
                <span className={agent.today.cost_usd > 0 ? styles.numCellPrimary : styles.numCellDim}>
                  {agent.today.cost_usd > 0 ? formatUSD(agent.today.cost_usd) : '—'}
                </span>
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
      <div className={styles.emptyPanel}>
        <svg className={styles.emptyIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
        </svg>
        <span className={styles.emptyTitle}>Sin tareas programadas activas</span>
        <span className={styles.emptyDesc}>Configura la primera tarea desde el panel de programación.</span>
      </div>
    )
  }

  return (
    <ul className={styles.taskList} role="list">
      {enabled.map((task, i) => {
        const id = task.trigger_id ?? task.task_id ?? task.id ?? String(i)
        return (
          <li key={id}>
            <button
              type="button"
              className={styles.taskRow}
              onClick={onRowClick}
              aria-label={`Ver tarea: ${taskLabel(task)}`}
            >
              <span className={styles.taskDot} aria-hidden="true" />
              <div className={styles.taskBody}>
                <div className={styles.taskName}>{taskLabel(task)}</div>
                {(task.cron ?? task.recurrence_human) && (
                  <div className={styles.taskRecurrence}>
                    {task.recurrence_human ?? task.cron}
                  </div>
                )}
              </div>
              <span className={styles.taskNextRun}>{taskNextRun(task)}</span>
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
          <Button variant="secondary" size="sm" onClick={load}>
            Actualizar
          </Button>
        }
      />

      <div className="view-body cv-view-body">

        {/* ── Loading ── */}
        {state.status === 'loading' && <DashboardSkeleton />}

        {/* ── Error ── */}
        {state.status === 'error' && (
          <FadeIn>
            <div className={styles.errorPanel} role="alert">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--color-danger)" strokeWidth="1.5" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              <p className={styles.errorMessage}>{state.message}</p>
              <p className={styles.errorHint}>Comprueba la conexión con el servidor y vuelve a intentarlo.</p>
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
            <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-8)' }}>

              {/* ── 1. Fila héroe de KPIs ── */}
              <StaggerItem>
                <div
                  className={styles.kpiGrid}
                  role="list"
                  aria-label="Métricas principales"
                >
                  <KpiCard
                    label="Gasto (7 días)"
                    value={formatUSD(summary.total_cost_usd)}
                    subtext="Últimos 7 días"
                    variant="highlight"
                    onClick={() => navigate('/coste')}
                  />
                  <KpiCard
                    label="Actividad (7 días)"
                    value={formatNumber(summary.cycles)}
                    suffix="acciones"
                    onClick={() => navigate('/coste')}
                  />
                  <KpiCard
                    label="Empleados activos"
                    value={activeAgents > 0 ? String(activeAgents) : '—'}
                    suffix={totalAgents > 0 ? `de ${totalAgents}` : undefined}
                    onClick={() => navigate('/agentes')}
                  />
                  <KpiCard
                    label="Pendientes de aprobar"
                    value={pendingCount > 0 ? String(pendingCount) : 'Ninguna'}
                    subtext={pendingCount > 0 ? 'Requieren tu atención' : 'Todo al día'}
                    variant={pendingCount > 0 ? 'alert' : 'default'}
                    onClick={() => navigate('/seguridad')}
                  />
                </div>
              </StaggerItem>

              {/* ── 2. Estado del sistema ── */}
              <StaggerItem>
                <section aria-label="Estado del sistema">
                  <SectionHeader title="Estado del sistema" />
                  <SystemHealth runtimeStatus={runtimeStatus} agentStats={agentStats} />
                </section>
              </StaggerItem>

              {/* ── 3. Mini serie temporal ── */}
              {chartPoints.length > 0 && (
                <StaggerItem>
                  <section aria-label="Gasto reciente">
                    <SectionHeader title="Gasto — últimos 7 días" />
                    <div className={styles.chartPanel}>
                      <ResponsiveContainer width="100%" height={148}>
                        <AreaChart data={chartPoints} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                          <defs>
                            <linearGradient id="dbCostGradient" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%"  stopColor="var(--color-accent)" stopOpacity={0.22} />
                              <stop offset="95%" stopColor="var(--color-accent)" stopOpacity={0.01} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid
                            strokeDasharray="3 3"
                            stroke="var(--color-border-subtle)"
                            vertical={false}
                          />
                          <XAxis
                            dataKey="day"
                            tickFormatter={formatDay}
                            tick={{ fontSize: 11, fill: 'var(--color-text-dim)', fontFamily: 'var(--font-ui)' }}
                            axisLine={false}
                            tickLine={false}
                            interval="preserveStartEnd"
                          />
                          <YAxis
                            tickFormatter={v => formatUSD(v as number)}
                            tick={{ fontSize: 11, fill: 'var(--color-text-dim)', fontFamily: 'var(--font-mono)' }}
                            axisLine={false}
                            tickLine={false}
                            width={60}
                          />
                          <Tooltip
                            content={<ChartTooltip />}
                            cursor={{ stroke: 'var(--color-border)', strokeWidth: 1 }}
                          />
                          <Area
                            type="monotone"
                            dataKey="value"
                            stroke="var(--color-accent)"
                            strokeWidth={2}
                            fill="url(#dbCostGradient)"
                            dot={false}
                            activeDot={{ r: 4, fill: 'var(--color-accent)', strokeWidth: 0 }}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  </section>
                </StaggerItem>
              )}

              {/* ── 4. Dos columnas: empleados + próximas tareas ── */}
              <StaggerItem>
                <div className={styles.twoCol}>
                  <section aria-label="Estado de empleados">
                    <SectionHeader
                      title="Por empleado"
                      onLinkClick={() => navigate('/agentes')}
                    />
                    <div className={styles.tablePanel}>
                      <EmployeeTable
                        agentStats={agentStats}
                        onRowClick={() => navigate('/agentes')}
                      />
                    </div>
                  </section>

                  <section aria-label="Próximas tareas programadas">
                    <SectionHeader
                      title="Próximas tareas"
                      onLinkClick={() => navigate('/programadas')}
                    />
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
