function tauri() {
    return window.__TAURI__;
}
export function isTauriRuntime() {
    return tauri() !== undefined;
}
/**
 * ASSUMED contract (not yet in contracts/app-engine.md — flagged in
 * UI-STATES.md / the handoff report): the wrapper forwards every NDJSON
 * line from the embedded CLI verbatim as the payload of this Tauri event, so
 * `EngineEvent` here matches app-engine.md §3 exactly. The core lane
 * (boot.rs) owns emitting it.
 */
const ENGINE_EVENT_CHANNEL = 'safent://engine-event';
/**
 * ASSUMED contract: emitted by the wrapper when it enters the `reconnecting`
 * phase (data-model.md EngineLifecycle) instead of navigating the window —
 * i.e. FR-012's safety net at the shell level, distinct from
 * frontend/src/components/ReconnectScreen.tsx which covers the SAME FR-012
 * once the product page itself is already loaded.
 */
const RECONNECT_CHANNEL = 'safent://reconnecting';
/** Subscribes to the engine event stream. Returns an unsubscribe function. */
export async function subscribeToEngineEvents(onEvent) {
    const api = tauri();
    if (!api)
        return () => { };
    return api.event.listen(ENGINE_EVENT_CHANNEL, (msg) => onEvent(msg.payload));
}
export async function subscribeToReconnect(onReconnect) {
    const api = tauri();
    if (!api)
        return () => { };
    return api.event.listen(RECONNECT_CHANNEL, (msg) => onReconnect(msg.payload.reason));
}
// ASSUMED command names — the core lane's embedded_cli.rs / bootstrap_service.rs
// (T009/T011, not present in this worktree) must implement them to match.
async function invoke(command) {
    const api = tauri();
    if (!api)
        return;
    await api.core.invoke(command);
}
/** "Cancelar": honest per contract §6 — the backend answers with a `failed` event. */
export function requestCancel() {
    return invoke('safent_cancel');
}
/** "Reintentar" on the one failure screen. */
export function requestRetry() {
    return invoke('safent_retry');
}
/** "Exportar diagnóstico" — FR-029, one gesture, no secrets by construction. */
export function exportDiagnostics() {
    return invoke('safent_export_diagnostics');
}
//# sourceMappingURL=ipc.js.map