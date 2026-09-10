import { initialState, reduceLifecycle, type UiState } from './lifecycle.js'
import { exportDiagnostics, requestCancel, requestRetry, subscribeToEngineEvents, subscribeToReconnect } from './ipc.js'
import { manageFocusOnTransition, render, type ScreenElements } from './render.js'

function requireElement<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id)
  if (!el) throw new Error(`safent-desktop: missing #${id} in index.html`)
  return el as T
}

function collectElements(): ScreenElements {
  return {
    preparing: requireElement('screen-preparing'),
    preparingStatus: requireElement('preparing-status'),
    preparingBar: requireElement('preparing-bar'),
    preparingStages: requireElement('preparing-stages'),
    cancelButton: requireElement('btn-cancel'),
    cancelNote: requireElement('cancel-note'),

    failed: requireElement('screen-failed'),
    failedHeading: requireElement('failed-heading'),
    failedHint: requireElement('failed-hint'),
    failedCode: requireElement('failed-code'),
    failedStage: requireElement('failed-stage'),
    failedDetail: requireElement('failed-detail'),
    retryButton: requireElement('btn-retry'),
    diagnosticsButton: requireElement('btn-diagnostics'),

    reconnecting: requireElement('screen-reconnecting'),
    reconnectingHeading: requireElement('reconnecting-heading'),
    reconnectingHint: requireElement('reconnecting-hint'),

    ready: requireElement('screen-ready'),
  }
}

function main(): void {
  const els = collectElements()
  let state: UiState = initialState
  render(state, els)

  const apply = (next: UiState): void => {
    const previousKind = state.kind
    state = next
    render(state, els)
    manageFocusOnTransition(previousKind, state, els)
  }

  void subscribeToEngineEvents((event) => apply(reduceLifecycle(state, { source: 'engine', event })))
  void subscribeToReconnect((reason) => apply(reduceLifecycle(state, { source: 'reconnect', reason })))

  els.cancelButton.addEventListener('click', () => {
    if (els.cancelButton.disabled) return
    void requestCancel()
  })

  els.retryButton.addEventListener('click', () => {
    if (els.retryButton.disabled) return
    apply(reduceLifecycle(state, { source: 'retry-requested' }))
    void requestRetry()
  })

  els.diagnosticsButton.addEventListener('click', () => {
    void exportDiagnostics()
  })

  // Disable the browser's own right-click menu on every screen this shell
  // ever shows (T013 — "sin menú contextual de navegador", FR-002). This
  // covers the loader window itself; window_policy.rs applies the SAME
  // script as a Tauri initialization_script so it also reaches the remote
  // product origin once the window navigates there.
  document.addEventListener('contextmenu', (e) => e.preventDefault())
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', main)
} else {
  main()
}
