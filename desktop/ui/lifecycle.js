// Ubiquitous language and event shapes come from
// specs/028-safent-app-nativa/contracts/app-engine.md §3. This module owns
// ZERO knowledge of Tauri, the DOM, or the CLI process — it is a pure state
// machine so the "una pantalla de fallo" / "cancelar sin dejar el equipo a
// medias" rules (FR-031..FR-033) are enforceable with plain unit tests.
export const initialState = { kind: 'preparing', stages: [], cancelable: true };
// Contract gap (see UI-STATES.md / handoff report): app-engine.md's `stage`
// event carries no "point of no return" flag (§6 only names one example,
// `applying_engine`, for the UPDATE flow — not bootstrap). Bootstrap itself
// is documented as always resumable ("ninguna etapa deja el equipo a
// medias"), so we default every stage to cancelable EXCEPT the ones that are
// known points of no return today. The core lane should replace this with an
// explicit signal on the wire.
const POINT_OF_NO_RETURN = new Set(['container']);
function isCancelable(activeStageId) {
    return !POINT_OF_NO_RETURN.has(activeStageId);
}
function upsertStage(stages, next) {
    const existing = stages.findIndex((s) => s.id === next.id);
    const entry = { ...next, status: 'active' };
    if (existing === -1)
        return [...stages, entry];
    const copy = stages.slice();
    copy[existing] = { ...copy[existing], ...entry };
    return copy;
}
function withStageUpdate(stages, id, patch) {
    return stages.map((s) => (s.id === id ? { ...s, ...patch } : s));
}
function stagesOf(state) {
    return state.kind === 'preparing' ? state.stages : [];
}
/** The single reducer driving the preparation / failure / reconnect screens. */
export function reduceLifecycle(state, action) {
    if (action.source === 'reconnect') {
        return { kind: 'reconnecting', reason: action.reason };
    }
    if (action.source === 'retry-requested') {
        return state.kind === 'failed' ? { ...state, retrying: true } : state;
    }
    const event = action.event;
    switch (event.t) {
        case 'stage': {
            const stages = upsertStage(stagesOf(state), {
                id: event.id,
                label: event.label,
                totalBytes: event.total_bytes,
            });
            return { kind: 'preparing', stages, cancelable: isCancelable(event.id) };
        }
        case 'progress': {
            const stages = withStageUpdate(stagesOf(state), event.id, {
                done: event.done,
                total: event.total,
                unit: event.unit,
            });
            const cancelable = state.kind === 'preparing' ? state.cancelable : isCancelable(event.id);
            return { kind: 'preparing', stages, cancelable };
        }
        case 'done': {
            const stages = withStageUpdate(stagesOf(state), event.id, { status: 'done', ms: event.ms });
            const cancelable = state.kind === 'preparing' ? state.cancelable : true;
            return { kind: 'preparing', stages, cancelable };
        }
        case 'failed':
            return {
                kind: 'failed',
                stageId: event.id,
                code: event.code,
                detail: event.detail,
                retryable: event.retryable,
                retrying: false,
            };
        case 'ready':
            return { kind: 'ready' };
        case 'facts':
            // Diagnostic-only payload today; no screen renders it (see UI-STATES.md).
            return state;
        default: {
            const exhaustive = event;
            return exhaustive;
        }
    }
}
/** The stage currently being worked on, for the headline of the preparation screen. */
export function activeStage(state) {
    if (state.kind !== 'preparing')
        return undefined;
    for (let i = state.stages.length - 1; i >= 0; i -= 1) {
        if (state.stages[i].status === 'active')
            return state.stages[i];
    }
    return state.stages[state.stages.length - 1];
}
//# sourceMappingURL=lifecycle.js.map