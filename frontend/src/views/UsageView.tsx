import { useEffect, useReducer, useCallback } from 'react'
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
  getUsageByAgent,
  getUsageTimeseries,
} from '../api/client'
import type {
  UsageSummary,
  UsageByAgent,
  UsageTimeseries,
  UsagePeriod,
  UsageDimension,
} from '../api/types'
import { PageHeader } from '../components/ui/PageHeader'
import { Button } from '../components/ui/Button'
import { Stagger, StaggerItem, FadeIn } from '../components/ui/motion'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatUSD(value: number): string {
  if (value >= 1) return `$${value.toFixed(2)}`
  if (value > 0) return `$${value.toFixed(4)}`
  return '$0.00'
}

function formatNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

function formatDay(day: string): string {
  // day is ISO date like "2026-06-20" — show "20 Jun"
  try {
    const d = new Date(day + 'T00:00:00')
    return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short' })
  } catch {
    return day
  }
}

// ── State machine ─────────────────────────────────────────────────────────────

interface UsageData {
  summary: UsageSummary
  byAgent: UsageByAgent
  timeseries: UsageTimeseries
}

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'success'; data: UsageData; period: UsagePeriod; dimension: UsageDimension }

type Action =
  | { type: 'LOADED'; data: UsageData; period: UsagePeriod; dimension: UsageDimension }
  | { type: 'FAILED'; message: string }
  | { type: 'RELOAD' }

function reducer(_state: State, action: Action): State {
  switch (action.type) {
    case 'LOADED': return { status: 'success', data: action.data, period: action.period, dimension: action.dimension }
    case 'FAILED': return { status: 'error', message: action.message }
    case 'RELOAD': return { status: 'loading' }
  }
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function SkeletonCard() {
  return <div className="cv-skeleton" style={{ height: 88, borderRadius: 'var(--r-md)' }} />
}

function SkeletonChartBlock() {
  return <div className="cv-skeleton" style={{ height: 200, borderRadius: 'var(--r-md)' }} />
}

// ── Hero stat card ─────────────────────────────────────────────────────────────

interface StatCardProps {
  label: string
  value: string
  suffix?: string
  highlight?: boolean
}

function StatCard({ label, value, suffix, highlight }: StatCardProps) {
  return (
    <div
      className="usage-stat-card"
      style={{
        background: highlight
          ? 'linear-gradient(135deg, color-mix(in srgb, var(--accent) 14%, var(--card)) 0%, var(--card) 100%)'
          : 'var(--card)',
        border: highlight
          ? '1px solid color-mix(in srgb, var(--accent) 30%, var(--line))'
          : '1px solid var(--line)',
        borderRadius: 'var(--r-md)',
        padding: 'var(--sp-5)',
        display: 'flex',
        flexDirection: 'column' as const,
        gap: 'var(--sp-2)',
        flex: 1,
        minWidth: 0,
      }}
    >
      <span style={{ fontSize: 'var(--text-label)', color: 'var(--ink3)', fontWeight: 500 }}>
        {label}
      </span>
      <span
        style={{
          fontSize: 'var(--text-title)',
          fontWeight: 650,
          color: 'var(--ink)',
          letterSpacing: '-0.03em',
          lineHeight: 1.2,
        }}
      >
        {value}
        {suffix && (
          <span style={{ fontSize: 'var(--text-label)', fontWeight: 400, color: 'var(--ink3)', marginLeft: 4 }}>
            {suffix}
          </span>
        )}
      </span>
    </div>
  )
}

// ── Period selector ───────────────────────────────────────────────────────────

interface PeriodSelectorProps {
  value: UsagePeriod
  onChange: (p: UsagePeriod) => void
  disabled: boolean
}

const PERIOD_OPTIONS: { value: UsagePeriod; label: string }[] = [
  { value: '7d', label: '7 días' },
  { value: '30d', label: '30 días' },
  { value: 'mtd', label: 'Este mes' },
]

function PeriodSelector({ value, onChange, disabled }: PeriodSelectorProps) {
  return (
    <div
      className="office-seg-toggle"
      role="group"
      aria-label="Periodo de análisis"
    >
      {PERIOD_OPTIONS.map(opt => (
        <button
          key={opt.value}
          type="button"
          className={`office-seg-btn${value === opt.value ? ' office-seg-btn--active' : ''}`}
          onClick={() => onChange(opt.value)}
          disabled={disabled}
          aria-pressed={value === opt.value}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

// ── Share bar ─────────────────────────────────────────────────────────────────

function ShareBar({ share }: { share: number }) {
  const pct = Math.min(100, Math.max(0, share * 100))
  return (
    <div
      style={{
        height: 4,
        background: 'var(--surface2)',
        borderRadius: 99,
        overflow: 'hidden',
        flex: 1,
        minWidth: 60,
      }}
      aria-hidden="true"
    >
      <div
        style={{
          height: '100%',
          width: `${pct.toFixed(1)}%`,
          background: 'var(--accent)',
          borderRadius: 99,
          transition: 'width 400ms var(--ease)',
        }}
      />
    </div>
  )
}

// ── Agent ranking table ────────────────────────────────────────────────────────

interface AgentRankingProps {
  byAgent: UsageByAgent
  onRowClick: (agentId: string) => void
}

function AgentRanking({ byAgent, onRowClick }: AgentRankingProps) {
  const agents = (byAgent.agents ?? []).slice().sort((a, b) => b.cost_usd - a.cost_usd)

  if (!byAgent.available || agents.length === 0) {
    return (
      <p className="cv-empty">Aún no hay actividad registrada por empleado.</p>
    )
  }

  return (
    <ul role="list" style={{ display: 'flex', flexDirection: 'column', gap: 4, listStyle: 'none' }}>
      {agents.map(agent => (
        <li key={agent.agent_id}>
          <button
            type="button"
            onClick={() => onRowClick(agent.agent_id)}
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
              transition: 'border-color var(--ease-hover), background var(--ease-hover), transform 120ms var(--ease)',
            }}
            className="usage-agent-row"
            aria-label={`Ver detalle de ${agent.name}`}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 'var(--text-body)', fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {agent.name}
              </div>
              <div style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', marginTop: 1 }}>
                {formatNumber(agent.cycles)} acciones · {(agent.share * 100).toFixed(0)}%
              </div>
            </div>
            <ShareBar share={agent.share} />
            <span style={{ fontSize: 'var(--text-body)', fontWeight: 600, color: 'var(--ink)', flexShrink: 0, minWidth: 56, textAlign: 'right' }}>
              {formatUSD(agent.cost_usd)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

// ── Model breakdown ──────────────────────────────────────────────────────────

interface ModelBreakdownProps {
  summary: UsageSummary
}

function ModelBreakdown({ summary }: ModelBreakdownProps) {
  const models = (summary.top_models ?? [])

  if (!summary.available || models.length === 0) {
    return (
      <p className="cv-empty">Sin datos de consumo por modelo en este periodo.</p>
    )
  }

  return (
    <ul role="list" style={{ display: 'flex', flexDirection: 'column', gap: 4, listStyle: 'none' }}>
      {models.map(m => {
        const isSelfHosted = m.cost_usd === 0
        return (
          <li
            key={m.model}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--sp-3)',
              padding: 'var(--sp-3) var(--sp-4)',
              background: 'var(--card)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--r-sm)',
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 'var(--text-body)', fontWeight: 600, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {m.model}
              </div>
              <div style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', marginTop: 1 }}>
                {(m.share * 100).toFixed(0)}% del consumo
              </div>
            </div>
            <ShareBar share={m.share} />
            <span style={{ fontSize: 'var(--text-body)', fontWeight: 600, color: isSelfHosted ? 'var(--ok)' : 'var(--ink)', flexShrink: 0, minWidth: 80, textAlign: 'right' }}>
              {isSelfHosted ? 'Cómputo propio' : formatUSD(m.cost_usd)}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// ── Governance row ─────────────────────────────────────────────────────────────

interface GovernanceRowProps {
  summary: UsageSummary
}

function GovernanceRow({ summary }: GovernanceRowProps) {
  const failurePct = summary.cycles > 0
    ? ((summary.failures / summary.cycles) * 100).toFixed(1)
    : '0.0'

  const hasData = summary.available && summary.cycles > 0

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
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
          flexDirection: 'column',
          gap: 'var(--sp-1)',
        }}
      >
        <span style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Acciones con incidencia
        </span>
        <span style={{ fontSize: 'var(--text-subtitle)', fontWeight: 650, color: summary.failures > 0 ? 'var(--warn)' : 'var(--ink)', letterSpacing: '-0.02em' }}>
          {hasData ? formatNumber(summary.failures) : '—'}
        </span>
        {hasData && (
          <span style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)' }}>
            {failurePct}% del total
          </span>
        )}
      </div>

      <div
        style={{
          background: 'var(--card)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-md)',
          padding: 'var(--sp-4)',
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--sp-1)',
        }}
      >
        <span style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Cómputo propio
        </span>
        <span style={{ fontSize: 'var(--text-subtitle)', fontWeight: 650, color: 'var(--ok)', letterSpacing: '-0.02em' }}>
          {hasData ? formatNumber(summary.self_hosted_cycles) : '—'}
        </span>
        {hasData && (
          <span style={{ fontSize: 'var(--text-caption)', color: 'var(--ink4)' }}>
            acciones sin coste externo
          </span>
        )}
      </div>
    </div>
  )
}

// ── Chart tooltip ─────────────────────────────────────────────────────────────

interface ChartTooltipProps {
  active?: boolean
  payload?: Array<{ value: number; name: string }>
  label?: string
  dimension: UsageDimension
}

function ChartTooltip({ active, payload, label, dimension }: ChartTooltipProps) {
  if (!active || !payload?.length) return null
  const value = payload[0]?.value ?? 0
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
      <div style={{ fontWeight: 600 }}>
        {dimension === 'cost' ? formatUSD(value) : `${formatNumber(value)} acciones`}
      </div>
    </div>
  )
}

// ── Empty / unavailable state ─────────────────────────────────────────────────

function EmptyUsage() {
  return (
    <div className="state-container" style={{ minHeight: 300 }}>
      <svg width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true" style={{ color: 'var(--ink4)', opacity: 0.5 }}>
        <rect x="6" y="6" width="36" height="36" rx="6" stroke="currentColor" strokeWidth="2" />
        <path d="M14 30l8-8 6 6 8-10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <p style={{ fontWeight: 600, color: 'var(--ink2)', fontSize: 'var(--text-body)' }}>
        Aún no hay actividad en este periodo
      </p>
      <p className="view-subtitle">
        El gasto y la actividad aparecerán aquí en cuanto el sistema procese acciones.
      </p>
    </div>
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

// ── Main view ─────────────────────────────────────────────────────────────────

export default function UsageView() {
  const navigate = useNavigate()
  const [state, dispatch] = useReducer(reducer, { status: 'loading' })

  // Selected period and dimension survive across re-fetches (derived from state on success)
  const currentPeriod: UsagePeriod = state.status === 'success' ? state.period : '30d'
  const currentDimension: UsageDimension = state.status === 'success' ? state.dimension : 'cost'

  const load = useCallback((period: UsagePeriod, dimension: UsageDimension) => {
    dispatch({ type: 'RELOAD' })
    Promise.all([
      getUsageSummary(period),
      getUsageByAgent(period),
      getUsageTimeseries(period, dimension),
    ]).then(([summary, byAgent, timeseries]) => {
      dispatch({
        type: 'LOADED',
        data: { summary, byAgent, timeseries },
        period,
        dimension,
      })
    }).catch((err: unknown) => {
      dispatch({
        type: 'FAILED',
        message: err instanceof Error ? err.message : 'No se pudo cargar el resumen de costes.',
      })
    })
  }, [])

  useEffect(() => { load(currentPeriod, currentDimension) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function handlePeriodChange(period: UsagePeriod) {
    load(period, currentDimension)
  }

  function handleDimensionToggle() {
    const next: UsageDimension = currentDimension === 'cost' ? 'tokens' : 'cost'
    if (state.status === 'success') {
      load(state.period, next)
    }
  }

  function handleAgentRowClick(_agentId: string) {
    // Project rule: every rendered element must be clickable. Navigate to agents view.
    navigate('/agentes')
  }

  const isLoading = state.status === 'loading'

  return (
    <>
      <PageHeader
        title="Coste"
        subtitle="Gasto y actividad del sistema por periodo."
        actions={
          <PeriodSelector
            value={currentPeriod}
            onChange={handlePeriodChange}
            disabled={isLoading}
          />
        }
      />

      <div className="view-body cv-view-body">

        {/* ── Loading skeleton ── */}
        {state.status === 'loading' && (
          <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-8)' }}>
            <StaggerItem>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 'var(--sp-3)' }}>
                {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
              </div>
            </StaggerItem>
            <StaggerItem><SkeletonChartBlock /></StaggerItem>
            <StaggerItem>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--sp-6)' }}>
                <SkeletonChartBlock />
                <SkeletonChartBlock />
              </div>
            </StaggerItem>
          </Stagger>
        )}

        {/* ── Error ── */}
        {state.status === 'error' && (
          <FadeIn>
            <div className="state-container" role="alert">
              <p className="state-error">{state.message}</p>
              <Button variant="secondary" onClick={() => load(currentPeriod, currentDimension)}>
                Reintentar
              </Button>
            </div>
          </FadeIn>
        )}

        {/* ── Success ── */}
        {state.status === 'success' && (() => {
          const { summary, byAgent, timeseries } = state.data
          const noData = !summary.available || (summary.cycles === 0 && summary.total_cost_usd === 0)
          const chartPoints = (timeseries.points ?? []).map(p => ({
            ...p,
            day: p.day,
            value: state.dimension === 'cost' ? p.cost_usd : p.cycles,
          }))

          if (noData) return <EmptyUsage />

          return (
            <Stagger style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-8)' }}>

              {/* ── 1. Hero row ── */}
              <StaggerItem>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                    gap: 'var(--sp-3)',
                  }}
                  role="list"
                  aria-label="Resumen del periodo"
                >
                  <StatCard
                    label="Gasto del periodo"
                    value={formatUSD(summary.total_cost_usd)}
                    highlight
                  />
                  <StatCard
                    label="Proyección"
                    value={formatUSD(summary.projected_cost_usd)}
                    suffix="a este ritmo"
                  />
                  <StatCard
                    label="Actividad"
                    value={formatNumber(summary.cycles)}
                    suffix="acciones"
                  />
                  <StatCard
                    label="Incidencias"
                    value={formatNumber(summary.failures)}
                  />
                </div>
              </StaggerItem>

              {/* ── 2. Time series chart ── */}
              <StaggerItem>
                <section aria-label="Gasto en el tiempo">
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--sp-3)', flexWrap: 'wrap', gap: 'var(--sp-2)' }}>
                    <SectionTitle>
                      {state.dimension === 'cost' ? 'Gasto en el tiempo' : 'Actividad en el tiempo'}
                    </SectionTitle>
                    <button
                      type="button"
                      className="cv-btn cv-btn--ghost cv-btn--sm"
                      onClick={handleDimensionToggle}
                      aria-label={`Cambiar a vista de ${state.dimension === 'cost' ? 'actividad' : 'gasto'}`}
                    >
                      {state.dimension === 'cost' ? 'Ver actividad' : 'Ver gasto'}
                    </button>
                  </div>
                  <div
                    style={{
                      background: 'var(--card)',
                      border: '1px solid var(--line)',
                      borderRadius: 'var(--r-md)',
                      padding: 'var(--sp-5)',
                    }}
                  >
                    {chartPoints.length === 0 ? (
                      <p className="cv-empty">Sin datos para este periodo.</p>
                    ) : (
                      <ResponsiveContainer width="100%" height={200}>
                        <AreaChart data={chartPoints} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                          <defs>
                            <linearGradient id="usageGradient" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="5%" stopColor="var(--accent)" stopOpacity={0.25} />
                              <stop offset="95%" stopColor="var(--accent)" stopOpacity={0.02} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid
                            strokeDasharray="3 3"
                            stroke="var(--line)"
                            vertical={false}
                          />
                          <XAxis
                            dataKey="day"
                            tickFormatter={formatDay}
                            tick={{ fontSize: 11, fill: 'var(--ink4)' }}
                            axisLine={false}
                            tickLine={false}
                            interval="preserveStartEnd"
                          />
                          <YAxis
                            tickFormatter={v =>
                              state.dimension === 'cost' ? formatUSD(v as number) : formatNumber(v as number)
                            }
                            tick={{ fontSize: 11, fill: 'var(--ink4)' }}
                            axisLine={false}
                            tickLine={false}
                            width={state.dimension === 'cost' ? 56 : 44}
                          />
                          <Tooltip
                            content={<ChartTooltip dimension={state.dimension} />}
                            cursor={{ stroke: 'var(--line2)', strokeWidth: 1 }}
                          />
                          <Area
                            type="monotone"
                            dataKey="value"
                            stroke="var(--accent)"
                            strokeWidth={2}
                            fill="url(#usageGradient)"
                            dot={false}
                            activeDot={{ r: 4, fill: 'var(--accent)', strokeWidth: 0 }}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    )}
                  </div>
                </section>
              </StaggerItem>

              {/* ── 3. Two-column: by agent + by model ── */}
              <StaggerItem>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
                    gap: 'var(--sp-6)',
                    alignItems: 'start',
                  }}
                >
                  <section aria-label="Gasto por empleado">
                    <SectionTitle>Por empleado</SectionTitle>
                    <AgentRanking byAgent={byAgent} onRowClick={handleAgentRowClick} />
                  </section>

                  <section aria-label="Gasto por modelo">
                    <SectionTitle>Por modelo</SectionTitle>
                    <ModelBreakdown summary={summary} />
                  </section>
                </div>
              </StaggerItem>

              {/* ── 4. Governance row ── */}
              <StaggerItem>
                <section aria-label="Gobernanza">
                  <SectionTitle>Gobernanza</SectionTitle>
                  <GovernanceRow summary={summary} />
                </section>
              </StaggerItem>

            </Stagger>
          )
        })()}

      </div>
    </>
  )
}
