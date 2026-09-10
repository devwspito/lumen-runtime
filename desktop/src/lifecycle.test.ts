import { describe, expect, it } from 'vitest'
import { activeStage, initialState, reduceLifecycle, type EngineEvent, type LifecycleAction, type UiState } from './lifecycle.js'

function engine(event: EngineEvent): LifecycleAction {
  return { source: 'engine', event }
}

function run(events: readonly LifecycleAction[]): UiState {
  return events.reduce(reduceLifecycle, initialState)
}

describe('reduceLifecycle — preparation stages (contract app-engine.md §3)', () => {
  it('starts with an empty, cancelable preparing state', () => {
    expect(initialState).toEqual({ kind: 'preparing', stages: [], cancelable: true })
  })

  it('adds a stage as active on `stage` and updates progress on `progress`', () => {
    const state = run([
      engine({ t: 'stage', id: 'runtime_staging', label: 'Preparando la base de ejecución', total_bytes: 90_000_000 }),
      engine({ t: 'progress', id: 'runtime_staging', done: 45_000_000, total: 90_000_000, unit: 'bytes' }),
    ])
    expect(state).toEqual({
      kind: 'preparing',
      cancelable: true,
      stages: [
        {
          id: 'runtime_staging',
          label: 'Preparando la base de ejecución',
          totalBytes: 90_000_000,
          status: 'active',
          done: 45_000_000,
          total: 90_000_000,
          unit: 'bytes',
        },
      ],
    })
  })

  it('marks a stage done without losing earlier stages, and activeStage() reports the live one', () => {
    const state = run([
      engine({ t: 'stage', id: 'preflight', label: 'Comprobando el equipo' }),
      engine({ t: 'done', id: 'preflight', ms: 120 }),
      engine({ t: 'stage', id: 'runtime_staging', label: 'Preparando la base de ejecución' }),
    ])
    expect(state.kind).toBe('preparing')
    if (state.kind !== 'preparing') throw new Error('unreachable')
    expect(state.stages.map((s) => [s.id, s.status])).toEqual([
      ['preflight', 'done'],
      ['runtime_staging', 'active'],
    ])
    expect(activeStage(state)?.id).toBe('runtime_staging')
  })

  it('tracks concurrent stages independently (pull_engine + pull_companion)', () => {
    const state = run([
      engine({ t: 'stage', id: 'pull_engine', label: 'Descargando Safent' }),
      engine({ t: 'stage', id: 'pull_companion', label: 'Descargando Anuncios' }),
      engine({ t: 'progress', id: 'pull_companion', done: 10, total: 100, unit: 'layers' }),
    ])
    if (state.kind !== 'preparing') throw new Error('unreachable')
    expect(state.stages).toHaveLength(2)
    expect(state.stages[1]).toMatchObject({ id: 'pull_companion', done: 10, total: 100 })
  })

  it('disables cancel once the point-of-no-return stage starts (container)', () => {
    const state = run([engine({ t: 'stage', id: 'container', label: 'Arrancando Safent' })])
    expect(state).toMatchObject({ kind: 'preparing', cancelable: false })
  })

  it('ignores `facts` events — no screen renders host facts', () => {
    const state = run([engine({ t: 'facts', facts: { os: 'macos' } })])
    expect(state).toBe(initialState)
  })
})

describe('reduceLifecycle — the ONE failure screen (FR-033)', () => {
  it('turns `failed` into the failed state with its cause, honestly', () => {
    const state = run([
      engine({ t: 'stage', id: 'pull_engine', label: 'Descargando Safent' }),
      engine({ t: 'failed', id: 'pull_engine', code: 'registry_unreachable', detail: 'dial tcp: timeout', retryable: true }),
    ])
    expect(state).toEqual({
      kind: 'failed',
      stageId: 'pull_engine',
      code: 'registry_unreachable',
      detail: 'dial tcp: timeout',
      retryable: true,
      retrying: false,
    })
  })

  it('a cancel is just a `failed` event per contract §6 — same one screen, no separate "cancelled" UI state', () => {
    const state = run([
      engine({ t: 'stage', id: 'machine', label: 'Preparando la máquina' }),
      engine({ t: 'failed', id: 'machine', code: 'machine_start_failed', detail: 'sigint', retryable: true }),
    ])
    expect(state.kind).toBe('failed')
  })

  it('"Reintentar" marks retrying only while already failed, and clears on the next real event', () => {
    const failed = run([engine({ t: 'failed', id: 'health', code: 'daemon_unhealthy', detail: 'x', retryable: true })])
    const retrying = reduceLifecycle(failed, { source: 'retry-requested' })
    expect(retrying).toMatchObject({ kind: 'failed', retrying: true })

    // clicking retry before any failure is a no-op — nothing to retry yet
    expect(reduceLifecycle(initialState, { source: 'retry-requested' })).toBe(initialState)

    const resumed = reduceLifecycle(retrying, engine({ t: 'stage', id: 'health', label: 'Comprobando salud' }))
    expect(resumed).toMatchObject({ kind: 'preparing' })
  })
})

describe('reduceLifecycle — reconnect safety net (FR-012, distinct from frontend/ReconnectScreen.tsx)', () => {
  it('a missing ticket resolves to ONE reconnecting state, not a request storm', () => {
    const state = reduceLifecycle(initialState, { source: 'reconnect', reason: 'token_missing' })
    expect(state).toEqual({ kind: 'reconnecting', reason: 'token_missing' })
  })

  it('an engine restart mid-session also resolves to reconnecting, with its own honest reason', () => {
    const ready = run([engine({ t: 'ready', endpoint_ref: 'stdout-secret' })])
    expect(ready).toEqual({ kind: 'ready' })
    const state = reduceLifecycle(ready, { source: 'reconnect', reason: 'engine_restarted' })
    expect(state).toEqual({ kind: 'reconnecting', reason: 'engine_restarted' })
  })
})
