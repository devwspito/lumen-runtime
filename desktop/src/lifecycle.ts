// Ubiquitous language and event shapes come from
// specs/028-safent-app-nativa/contracts/app-engine.md §3. This module owns
// ZERO knowledge of Tauri, the DOM, or the CLI process — it is a pure state
// machine so the "una pantalla de fallo" / "cancelar sin dejar el equipo a
// medias" rules (FR-031..FR-033) are enforceable with plain unit tests.

export type StageId =
  | 'preflight'
  | 'runtime_staging'
  | 'machine'
  | 'pull_engine'
  | 'pull_companion'
  | 'container'
  | 'health'
  | 'companion_scaffold'
  | 'companion_up'
  | 'companion_reload'
  | 'backup'
  | 'restore'
  | 'cleanup'

export type ProgressUnit = 'bytes' | 'layers' | 'steps'

// Closed, stable set (contract §3). Anything else arriving on the wire is a
// contract violation, not a reason to crash the UI — see failure-copy.ts.
export type FailureCode =
  | 'unsupported_os'
  | 'unsupported_arch'
  | 'insufficient_disk'
  | 'insufficient_memory'
  | 'runtime_hash_mismatch'
  | 'machine_create_failed'
  | 'machine_start_failed'
  | 'userns_blocked'
  | 'helper_denied'
  | 'registry_unreachable'
  | 'digest_mismatch'
  | 'pull_interrupted'
  | 'port_exhausted'
  | 'container_start_failed'
  | 'daemon_unhealthy'
  | 'companion_network_conflict'
  | 'companion_migration_failed'
  | 'companion_unreachable'
  | 'backup_failed'
  | 'restore_failed'
  | 'clock_skew'

/** One NDJSON line from `safent <verb> --porcelain`, forwarded verbatim. */
export type EngineEvent =
  | { t: 'stage'; id: StageId; label: string; total_bytes?: number }
  | { t: 'progress'; id: StageId; done: number; total?: number; unit: ProgressUnit }
  | { t: 'done'; id: StageId; ms: number }
  | { t: 'failed'; id: StageId; code: FailureCode; detail: string; retryable: boolean }
  | { t: 'facts'; facts: unknown }
  | { t: 'ready'; endpoint_ref: 'stdout-secret' }

/**
 * Inputs to the state machine beyond the CLI's own NDJSON: the wrapper's own
 * FR-012 safety net (no valid ticket to navigate to) and the owner clicking
 * "Reintentar". Both are ASSUMED integration points — see the handoff notes
 * in UI-STATES.md — because the module that would emit them (boot.rs) does
 * not exist in this worktree yet.
 */
export type LifecycleAction =
  | { source: 'engine'; event: EngineEvent }
  | { source: 'reconnect'; reason: 'token_missing' | 'engine_restarted' }
  | { source: 'retry-requested' }

export interface StageProgress {
  readonly id: StageId
  readonly label: string
  readonly totalBytes?: number
  readonly done?: number
  readonly total?: number
  readonly unit?: ProgressUnit
  readonly status: 'active' | 'done'
  readonly ms?: number
}

/**
 * Discriminated union — the screen you render is a pure function of this
 * type, so "cargar sin vale" and "un fallo" can never both be true at once
 * (the impossible-states-impossible rule).
 */
export type UiState =
  | { readonly kind: 'preparing'; readonly stages: readonly StageProgress[]; readonly cancelable: boolean }
  | { readonly kind: 'ready' }
  | {
      readonly kind: 'failed'
      readonly stageId: StageId
      readonly code: FailureCode
      readonly detail: string
      readonly retryable: boolean
      readonly retrying: boolean
    }
  | { readonly kind: 'reconnecting'; readonly reason: 'token_missing' | 'engine_restarted' }

export const initialState: UiState = { kind: 'preparing', stages: [], cancelable: true }

// Contract gap (see UI-STATES.md / handoff report): app-engine.md's `stage`
// event carries no "point of no return" flag (§6 only names one example,
// `applying_engine`, for the UPDATE flow — not bootstrap). Bootstrap itself
// is documented as always resumable ("ninguna etapa deja el equipo a
// medias"), so we default every stage to cancelable EXCEPT the ones that are
// known points of no return today. The core lane should replace this with an
// explicit signal on the wire.
const POINT_OF_NO_RETURN: ReadonlySet<StageId> = new Set<StageId>(['container'])

function isCancelable(activeStageId: StageId): boolean {
  return !POINT_OF_NO_RETURN.has(activeStageId)
}

function upsertStage(
  stages: readonly StageProgress[],
  next: Pick<StageProgress, 'id' | 'label' | 'totalBytes'>,
): readonly StageProgress[] {
  const existing = stages.findIndex((s) => s.id === next.id)
  const entry: StageProgress = { ...next, status: 'active' }
  if (existing === -1) return [...stages, entry]
  const copy = stages.slice()
  copy[existing] = { ...copy[existing], ...entry }
  return copy
}

function withStageUpdate(
  stages: readonly StageProgress[],
  id: StageId,
  patch: Partial<StageProgress>,
): readonly StageProgress[] {
  return stages.map((s) => (s.id === id ? { ...s, ...patch } : s))
}

function stagesOf(state: UiState): readonly StageProgress[] {
  return state.kind === 'preparing' ? state.stages : []
}

/** The single reducer driving the preparation / failure / reconnect screens. */
export function reduceLifecycle(state: UiState, action: LifecycleAction): UiState {
  if (action.source === 'reconnect') {
    return { kind: 'reconnecting', reason: action.reason }
  }

  if (action.source === 'retry-requested') {
    return state.kind === 'failed' ? { ...state, retrying: true } : state
  }

  const event = action.event
  switch (event.t) {
    case 'stage': {
      const stages = upsertStage(stagesOf(state), {
        id: event.id,
        label: event.label,
        totalBytes: event.total_bytes,
      })
      return { kind: 'preparing', stages, cancelable: isCancelable(event.id) }
    }
    case 'progress': {
      const stages = withStageUpdate(stagesOf(state), event.id, {
        done: event.done,
        total: event.total,
        unit: event.unit,
      })
      const cancelable = state.kind === 'preparing' ? state.cancelable : isCancelable(event.id)
      return { kind: 'preparing', stages, cancelable }
    }
    case 'done': {
      const stages = withStageUpdate(stagesOf(state), event.id, { status: 'done', ms: event.ms })
      const cancelable = state.kind === 'preparing' ? state.cancelable : true
      return { kind: 'preparing', stages, cancelable }
    }
    case 'failed':
      return {
        kind: 'failed',
        stageId: event.id,
        code: event.code,
        detail: event.detail,
        retryable: event.retryable,
        retrying: false,
      }
    case 'ready':
      return { kind: 'ready' }
    case 'facts':
      // Diagnostic-only payload today; no screen renders it (see UI-STATES.md).
      return state
    default: {
      const exhaustive: never = event
      return exhaustive
    }
  }
}

/** The stage currently being worked on, for the headline of the preparation screen. */
export function activeStage(state: UiState): StageProgress | undefined {
  if (state.kind !== 'preparing') return undefined
  for (let i = state.stages.length - 1; i >= 0; i -= 1) {
    if (state.stages[i].status === 'active') return state.stages[i]
  }
  return state.stages[state.stages.length - 1]
}
