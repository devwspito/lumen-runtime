# Estados de la ventana (`desktop/src`)

**Preparando** (`kind: 'preparing'`) — pantalla por defecto: «Preparando
Safent», texto en vivo (`aria-live="polite"`) con la etapa activa
(`stage.label`, ya en español del CLI). Lista de etapas «Hecho»/«En curso»
con progreso real («43 de 86 MB»), nunca un porcentaje inventado. «Cancelar»
activo hasta el punto de no retorno (hoy, `container`); después se
deshabilita con nota: «Esta fase ya no se puede cancelar; espera a que
termine.»

**Fallo** (`kind: 'failed'`) — la ÚNICA pantalla de error (FR-033). Titular
en lenguaje del dueño según `FailureCode` (`failure-copy.ts`; nunca
«podman»/«contenedor»/«VM» fuera de «Detalles»). «Reintentar» (oculto si no
`retryable`; «Reintentando…» sin doble envío) y «Exportar diagnóstico».
`<details>` con Código, Etapa, Mensaje técnico. Cancel también resuelve
aquí: el contrato lo trata como un `failed` más.

**Reconectando** (`kind: 'reconnecting'`) — red de seguridad de
FR-012/SC-012, no el recorrido normal. Dos motivos: `token_missing` («Safent
necesita volver a autorizar esta ventana») y `engine_restarted` («Safent se
reinició. Un momento mientras vuelve a conectar»). No llama a ningún
endpoint: espera la señal del envoltorio, sin ráfagas de reintentos.

**Listo** (`kind: 'ready'`) — transición breve antes de que el envoltorio
navegue al producto.

Foco: al ENTRAR en Fallo o Reconectando, el foco salta al título (NFR-005);
un re-render del mismo estado (p. ej. un `progress`) no lo roba de vuelta.

## Huecos de contrato para la línea del núcleo

`app-engine.md` no define: (1) el canal Tauri NDJSON→webview — asumido
`safent://engine-event`, payload = `EngineEvent` tal cual; (2) la señal de
reconexión — asumida `safent://reconnecting`, `{reason}`; (3) un
`FailureCode` para «cancelado por el dueño» (§6 solo dice que SIGINT emite
un `failed` cualquiera); (4) un flag de punto-de-no-retorno en `stage` (hoy
asumido en cliente: `container`). `window_policy.rs` emite
`safent://restart-engine-requested` y `safent://quit-requested` sin
implementar el apagado real — eso es de `boot.rs`.
