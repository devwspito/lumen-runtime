# 025 — Resultados de la matriz completa (168 filas) sobre la DGX

Ejecución 2026-09-10 en el host DGX (`linux/aarch64`, podman **rootless** 4.9.3) contra las
imágenes finales: runtime `localhost/safent-runtime:latest` (== `:0.8.42`, label
`org.opencontainers.image.revision=607c98e`) y companion `localhost/safent-ads:local`
(safent-ads `main` 17b586e), seleccionado automáticamente por `run-safent.sh` al no exportar
`SAFENT_ADS_IMAGE`.

Instancia única `matriz-final-1` (volumen `matriz-final-1-data`, puerto `127.0.0.1:18090`,
estado del companion en el scratchpad de la sesión, **nunca** `~/.safent`), lanzada con:

```sh
SAFENT_NAME=matriz-final-1 SAFENT_VOLUME=matriz-final-1-data \
SAFENT_COMPANION_STATE=$SCRATCH/safent-state/companions/ads \
./ops/container/run-safent.sh localhost/safent-runtime:latest 18090
```

Estado del host antes de empezar: `podman ps -a` sólo con tres contenedores `safent-*`
apagados de pasadas antiguas (`safent-diag`, `safent-v839`, `safent-audit` — no tocados) y
`podman network ls` **sin** `safent-companions`: no había companion ni red ajena que
respetar, así que la instancia de esta pasada es la única que existe.

Auth de la API: `GET /app/?k=$(podman exec matriz-final-1 cat
/var/lib/hermes-bootstrap/bootstrap/webui-bootstrap)` → bearer inyectado en
`window.__SAFENT_TOKEN__`. Ninguna credencial real en toda la pasada (proveedores, Composio,
Brave, Tailscale, Google/Meta: sólo valores falsos o `[DUEÑO]`).

**Leyenda:** `PASS` · `FALLA` · `[DUEÑO]` (necesita credencial/cuenta real del dueño) ·
`NO-APLICA-DGX` (sólo tiene sentido en macOS/Windows/Tauri/instalador). El orden de las
secciones es el **orden de ejecución** (destructivo al final), no el orden del documento de
la matriz; cada fila lleva su id.

---

## §0 Instalación y arranque (INST)

| id | resultado | cómo | evidencia |
|---|---|---|---|
| INST-01 | **PASS** | `run-safent.sh localhost/safent-runtime:latest 18090` + `curl localhost:18090/healthz` | Provisionado del companion OK (`ads-migrate` `ExitCode=0`, los 4 servicios `Up`, `ads-db`/`ads-api` `healthy`); `podman run` devolvió 16:28:20, `Startup finished in 2.520s`, `Started hermes-runtime.service` 16:28:22, `/healthz` **200 al primer sondeo** (16:28:28, +8 s) |
| INST-02 | **PASS** | `podman exec matriz-final-1 systemctl is-system-running` / `--failed` | `running`, **0 unidades failed**; 20 unidades `hermes-*` cargadas: `hermes-runtime`, `hermes-shell-server`, `hermes-egress-proxy`, `hermes-browser-netns`, `hermes-mcp-launcher`, `hermes-companion-egress`, `hermes-exec-launcher`, `hermes-audit-tail`, `hermes-host-firewall`, `hermes-keygen`, 4 `hermes-mcp-*`, y los 3 `.path` (config-sync, tailscaled, tailscale-control) `active waiting` |
| INST-03 | **PASS** | `cat /sys/kernel/security/lsm`; `systemctl show hermes-landlock-assert -p Result`; `printenv HERMES_RUNTIME_LANDLOCK_ALLOW_DEGRADE` | LSM = `lockdown,capability,landlock,yama,apparmor,ima,evm`; `Result=success`, `ExecMainStatus=0`; journal: `hermes-landlock-assert: Landlock LSM activo — OK`, `landlock_loader.applied abi=7 rules=13 mask=0x17bf`, `runtime_landlock.applied outcome=applied enforcing=True`, `confinement_check.PASS`. Sin variable de degradación. Caso negativo (kernel sin Landlock) **no ejecutable** en esta máquina: un único kernel |
| INST-04 | **PASS** | `curl "localhost:18090/app/?k=<webui-bootstrap>"` | 200 y `window.__SAFENT_TOKEN__="f111b6ac…"` (64 hex) inyectado en `index.html`; el bearer funciona en todas las llamadas posteriores |
| INST-05 | **PASS** | `curl -i localhost:18090/api/v1/agents` y `.../api/v1/runtime/agent-stream` sin cabecera | ambas **401**; con bearer, `/agents` → 200. WebSockets sin token: ver `SEG-VNC` en §Seguridad |
| INST-06 | **PASS** | `GET /api/v1/mcp` tras el arranque | `safent-ads` con `health:"healthy"`, **`tool_count: 61`**, `companion_status:"listo"`; `argv` con `mcp-remote@0.8.6`; journal `hermes.dbus.companion_seed_imported slug=safent-ads` a los ~2 s del arranque. Semillas de oficina: excel 25, word 54, powerpoint 37, todas `healthy` |
| INST-07 | **PASS** | ver §Recreación final (`--no-companion` sobre el mismo nombre, tras el desmontaje de la instancia principal) | fila resuelta al final del informe |
| INST-08 | **PASS** | ver §Recreación final (`--codex-auth` con `auth.json` dummy) | fila resuelta al final del informe; con cuenta real de Codex sería `[DUEÑO]` |

**Smoke** (INST-01/02/04/05 + CHAT-01 + AGT-01 + MCP-01 + SEG-01/18): todo verde a los 8 s
del arranque — `GET /security/kill-switch` → `{"engaged":false}`, `GET /egress/mode` →
`{"mode":"deny"}` (deny-by-default confirmado en instalación nueva), `GET /agents/roster` →
departamentos poblados, `GET /instance/features` → `edition:"community"`.

## §2 Chat (CHAT)

| id | resultado | cómo | evidencia |
|---|---|---|---|
| CHAT-01 | **PASS** (con matiz) | `POST /api/v1/chat {"user_message":"hola matriz"}` sin proveedor + `frontend/src/views/ChatView.tsx:431-447` | `NoModelBanner` (`role="alert"`) con `chat.nomodel.text` + CTA que navega a `/proveedores`. El turno **sí** se acepta (200 `task_id`) y el stream termina `outcome:"failed"` con `HermesModelNotConfiguredError: HERMES_MODEL no está definido…`: el compositor no se desactiva (`canSend` no mira el proveedor). Ningún proveedor contactado (no hay ninguno), pero el usuario ve un error técnico en vez de un bloqueo |
| CHAT-02 | **[DUEÑO]** (mecánica **PASS**) | `POST /chat` → `GET /chat/stream/{task_id}?token=…` | SSE completo con ids: `id:1 {"kind":"status","status":"in_progress"}` → `id:2 {"kind":"done","outcome":"failed"}`. La respuesta real de un modelo exige clave/cuenta del dueño |
| CHAT-03 | **[DUEÑO]** | requiere stream vivo de un modelo real | sin modelo el stream muere en <1 s; no hay ventana donde pulsar `chat.stop` |
| CHAT-04 | **[DUEÑO]** | idem (reconexión con `Last-Event-ID` a media respuesta) | el protocolo numera eventos (`id:` incremental, visto arriba), que es la precondición del fix; la reanudación real necesita un turno largo |
| CHAT-05 | **PASS** | `POST /api/v1/workspace/files` (multipart) | 201 `{"name":"f1.txt","path":"/var/lib/hermes/workspace/f1.txt","size":47}`; visible en `GET /workspace/files` |
| CHAT-06 | **[DUEÑO]**/parcial | límite de adjunto | el error inline existe (`chat.err.attach`, `ChatView.tsx:720`); no se ejercitó con un fichero por encima del tope (no hay tope declarado en el cliente, el rechazo es del backend) |
| CHAT-07 | **NO-APLICA-DGX** | File System Access API | no hay navegador Chrome/Edge en el host |
| CHAT-08 | **NO-APLICA-DGX** | idem con Firefox | — |
| CHAT-09 | **[DUEÑO]** | delegación a especialista | necesita modelo real que emita `delegate_task` |
| CHAT-10 | **[DUEÑO]** | chip "usando el navegador" | necesita modelo real que llame `browser_navigate` |
| CHAT-11 | **PASS** (código) | `frontend/src/components/Layout.tsx:405-432` | la lista recorta y pinta `layout.recents.more`/`layout.recents.less` con `aria-expanded`; `GET /chat/conversations` devuelve las conversaciones creadas en esta pasada |
| CHAT-12 | **PASS** (código) | `Layout.tsx:507-512` | botón `layout.new_chat` presente en el sidebar |

## §3 Agentes (AGT)

| id | resultado | cómo | evidencia |
|---|---|---|---|
| AGT-01 | **PASS** | `GET /api/v1/agents/roster` | 200, departamentos agrupados (`cerebro`/CEO con el agente `default`) |
| AGT-02 | **PASS** | `GET /api/v1/agents/default-roster` | `{"enabled":false}` en fresco (CE siembra sólo `default`) |
| AGT-03 | **PASS** | `POST /api/v1/agents {"name":"Matriz Tester","department":"cerebro"}` | 200 `agent_id=7fb30686762f40b8807ebeed43a41d31` |
| AGT-04 | **PASS** | `PATCH /api/v1/agents/{id} {"name":"Matriz Tester 2"}` | 200 con el nombre nuevo |
| AGT-05 | **PASS** | ver §Destructivo | `DELETE /agents/{id}` |
| AGT-06 | **PASS** (por diseño, no hay activo global) | `POST /agents/{id}/activate` → `GET /agents/active` | activate → `{"ok":true,"active_agent_id":"7fb3…","deprecated":true}`; `GET /agents/active` → **200** `{"active_agent_id":"","deprecated":true}` (antes 405 — hallazgo #7 cerrado). Ambos son no-ops declarados: el binding es por conversación (`agents_api.py:195-210`), así que la UI nunca podrá "conocer el agente activo" por esta vía |
| AGT-07 | **PASS** | `GET /api/v1/runtime/agent-stream?token=…` | SSE empuja `RuntimeSnapshot` completo (`runtime.state`, `active_task_count`, `stats.agents[]`) sin polling |
| AGT-08 | **NO-APLICA-DGX** | backoff de reconexión con throttling de DevTools | sin navegador |
| AGT-09 | **NO-APLICA-DGX** | piso pixel (canvas) | sin navegador |
| AGT-10 | **PASS** | `POST /api/v1/tasks/scheduled {"label":"matriz-cron","instruction":"di hola","cron":"* * * * *"}` | 201 `{"ok":true,"trigger_id":"d3dff588-d5d0-47e7-bc22-9286df0f0cd6"}` (UUID completo) |
| AGT-11 | **PASS** | `GET /tasks/configured` → `GET /tasks/scheduled/{trigger_id}` | la lista devuelve **el mismo UUID** y el detalle responde **200** (dead-end lista→detalle sigue cerrado) |
| AGT-12 | **PASS** | `POST /tasks/scheduled/{id}/enabled {"enabled":false\|true}` | 200 `{"ok":true}` en ambos sentidos (nada de `{"ok":false}` bajo 200) |
| AGT-13 | **PASS** | ver §Destructivo | `DELETE /tasks/scheduled/{id}` |
| AGT-14 | **PASS — regresión R14 CERRADA** | dejar disparar el cron 2 min → `GET /api/v1/tasks/recent` y `GET /tasks/configured` | `/tasks/recent` ya **no** está vacío: 3 entradas con `label`, `trigger_kind:"timer"`, `enqueued_at`; `configured` trae `last_run_at:"2026-09-10T14:31:22.517712+00:00"`, `last_status:"pending"` y `next_run_at` **avanzando** (14:33). Journal: 2× `hermes.triggers.timer.fired`, `tasks.loop.task_claimed`/`task_failed` (falla por no haber modelo, esperado). Matiz: `last_status` se queda en `pending` aunque el bucle ya reportó `task_failed` — el estado terminal no vuelve a la fila |

## §4 Habilidades (SKL)

| id | resultado | cómo | evidencia |
|---|---|---|---|
| SKL-01 | **PASS** | `GET /api/v1/skills/hub/search?q=git` | 200 con 8+ resultados (`official/research/gitnexus-explorer`, `skills-sh/dalestudy/skills/git`…) |
| SKL-02 | **PASS** | `POST /skills/hub/install {"identifier":"skills-sh/dalestudy/skills/git","force":false}` | 202 `{"op_id":…}` → `GET /skills` lista `native:git` (`signed_at` 14:33:32) |
| SKL-03 | **PASS** | mismo POST con `official/research/gitnexus-explorer` | 202 `{"ok":false,"blocked":true,"score":30,"verdict":"FAIL","risks":[…privilege escalation…, CRITICAL executes raw contents…]}` — sin `force` no continúa |
| SKL-04 | **PASS** (backend) — ver §Seguridad para el flujo de UI con UN solo TOTP | `force:true` sin MFA | **403** `{"code":"mfa_not_enrolled"}` (el bypass del hallazgo #4 sigue cerrado) |
| SKL-05 | **PASS** | ver §Destructivo | `DELETE /skills/hub/{name}` |
| SKL-06 | **PASS** | `GET /api/v1/skills/native:git/details` | 200 con el paquete completo |
| SKL-07 | **PASS** (parcial, no ejercitable) | `POST /skills/native:git/promote {"confirm":true}` | 404 `skill not found` con el id que usa la UI (`package_id`), **pero** el botón "Promover" sólo se pinta si `isValidated` (`SkillsView.tsx:708`) y una skill `native` no lo está: no hay dead-end alcanzable desde la UI. Sin skill `validated` (la única fábrica era enseñar por navegador, retirada) la fila no es ejercitable |
| SKL-08 | **[DUEÑO]** | `skills.verify` inserta un turno de chat | necesita modelo real |
| SKL-09 | **NO-APLICA — capacidad retirada** | `POST /api/v1/training`, `/api/v1/training/{id}/start`, `/teach/vnc`; grep del bundle | las tres rutas → **404**; **cero** referencias a `TeachModal`/`startTeaching`/`skills.teach` en `frontend/src`; `/app/ensenar` redirige a `/capacidades?tab=en-vivo` (`App.tsx:109`). Coincide con `retirada-ensenar.md` |
| SKL-10 | **NO-APLICA — capacidad retirada** | idem | no hay sesión que abandonar; `GET /skills` no deja huérfanas |

## §5 Integraciones (INTG)

| id | resultado | cómo | evidencia |
|---|---|---|---|
| INTG-01 | **PASS** | `GET /api/v1/integrations/composio/status` en fresco | `{"has_key":false,"enabled":false,"entity_id":"default"}` — la UI no llama a `connected`/`toolkits` en ese estado |
| INTG-02 | **[DUEÑO]** | clave Composio real | — |
| INTG-03 | **[DUEÑO]** | OAuth de un toolkit real | — |
| INTG-04 | **[DUEÑO]** | depende de INTG-03 | — |
| INTG-05 | **PASS** | `GET /api/v1/web-search/status` | `{"brave":false,"tavily":false,"exa":false,"ddgs_fallback":true}` |
| INTG-06 | **[DUEÑO]** | clave Brave real | — |
| INTG-07 | **PASS** | `POST /web-search/key {"provider":"brave","api_key":""}` | **422** `string_too_short` (el backend también valida); la UI corta antes con `int.brave.err.enter_key` |
| INTG-08 | **[DUEÑO]** | fallback DDGS desde el chat | necesita modelo activo que dispare `web_search` |

## §6 Herramientas / MCP (MCP)

| id | resultado | cómo | evidencia |
|---|---|---|---|
| MCP-01 | **PASS** | `GET /api/v1/mcp` | excel 25 / word 54 / powerpoint 37 `healthy` + `safent-ads` 61 `healthy` `companion_status:"listo"` |
| MCP-02 | **PASS** | `POST /api/v1/mcp {"server_id":"memory","argv":["npx","-y","@modelcontextprotocol/server-memory"]}` | **201 en 7 s** `{"ok":true,"tool_count":9}`; la lista lo da `healthy` con **11** tools (misma discrepancia 9≠11 de las dos pasadas anteriores) |
| MCP-03 | **PASS** | `GET /api/v1/mcp/registry?q=memory&limit=5` | 200 con resultados externos (`ai.justonce/memory`, con `unsupported_reason` cuando sólo es remoto) |
| MCP-04 | **FALLA — el EXDEV del hallazgo #5 SIGUE ABIERTO** | `POST /api/v1/mcp {"server_id":"time","argv":["uvx","mcp-server-time"]}` (paquete nunca cacheado, volumen nuevo) | 400 `prefetch falló … (rc=1): Failed to download 'tzlocal==5.4.4' … failed to rename file from /var/lib/hermes/uv-cache/.tmpHyj2Z5 to /var/lib/hermes/uv-cache/archive-v0/xaY-8j_kZQF9Yebj: Invalid cross-device link (os error 18)`. Contexto medido dentro de la jaula: `UV_CACHE_DIR=/var/lib/hermes/uv-cache`, `TMPDIR=/var/lib/hermes/tmp`, ambos en `/dev/nvme0n1p2` según `df` — el rename falla igualmente. **Ningún MCP de Python nuevo es instalable** |
| MCP-05 | **FALLA** | `POST /api/v1/mcp` con `env` (el propio ejemplo del formulario) | el `<textarea>` de `mcp.env.label` sugiere `BRAVE_API_KEY=br-xxx` (`McpView.tsx:1148`) y el backend responde **400** `clave de env no permitida: 'SECRET_TOKEN' (allowlist: ['ADS_BEARER','CONTEXT7_API_KEY','HOME','MCP_REMOTE_CONFIG_DIR','NODE_EXTRA_CA_CERTS','OD_*','OPENAI_*','REPLICATE_API_TOKEN','XDG_CONFIG_HOME'])` — `BRAVE_API_KEY` **no** está en `_MCP_BYOK_ENV_KEYS` (`dbus_runtime_service.py:7141-7168`). Quien siga el ejemplo de la UI recibe un error crudo del backend |
| MCP-06 | **PASS** | `POST /mcp {"argv":["bash","-c","echo hi"]}` | **400** `runner 'bash' no permitido (allowlist: ['npx','pipx','uvx'])` |
| MCP-07 | **PASS** | `POST /mcp/managed-remote/no-existe/connect` | **400** `slug 'no-existe' no es un servidor MANAGED_REMOTE conocido` |
| MCP-08 | **PASS** (por el companion, no por la URL manual) | companion arriba + `POST /mcp/managed-remote/safent-ads/connect {"url":"https://ads.safent.internal:8443/mcp"}` | el seed ya deja `safent-ads` conectado con 61 tools y la entrada "Anuncios" del sidebar; el connect manual con **esa misma URL** responde 400 `managed_remote endpoint must use port 443 (got 8443)` — el camino manual sólo sirve para un self-hosted en 443 |
| MCP-09 | **PASS** (validación) / **[DUEÑO]** (éxito) | `POST … {"url":"http://…"}` | **400** `managed_remote endpoint must use https:// (got 'http://')`; conectar de verdad exige un MCP de Ads propio del dueño |
| MCP-10 | **PASS** | `DELETE /api/v1/mcp/memory` y `/mcp/gh-test` | **204** en ambos; desaparecen de la lista |
| MCP-11 | **PASS** | `GET /mcp` sobre una entrada con env | el payload devuelve `argv`/`health`/`tool_count`; no expone valores de env en claro |
| MCP-12 | **PASS** | `POST /api/v1/security/scans/install {"kind":"mcp","identifier":"npx:evil-dropper-mcp"}` | 200 `score:45, verdict:"WARN", requires_owner_approval:true, engine:"trivy"` con riesgos CVE listados |

## §7 En vivo (LIVE)

| id | resultado | cómo | evidencia |
|---|---|---|---|
| LIVE-01 | **PASS** | `GET /api/v1/runtime/status` en fresco + `EnVivoView.tsx:75-79` | `{"state":"idle","activity":[],"browser_live":false}`; la vista pinta `envivo.no_tasks` y **no** monta `VncFrame` |
| LIVE-02 | **[DUEÑO]** | requiere modelo real que dispare `browser_navigate` | el frame se monta sólo con `status.browser_live === true` (`EnVivoView.tsx:40-44`), no por el nombre de la tool |
| LIVE-03 | **[DUEÑO]** | idem con destino bloqueado por egress | misma condición de código: sin página real `browser_live` sigue `false`, el frame no aparece |
| LIVE-04 | **PASS** | `POST /api/v1/tasks/{task_id}/cancel` sobre una tarea de cron encolada | 200 `{"ok":true,"requested":true}` |
| LIVE-05 | **PASS** | `grep -c teach frontend/src/views/EnVivoView.tsx` | **0** coincidencias: la vista es sólo monitor + detener, coherente con la retirada de "enseñar" |

## §8 Seguridad (SEG) y §19 SSH gobernado

| id | resultado | cómo | evidencia |
|---|---|---|---|
| SEG-01 | **PASS** | `GET /api/v1/security/kill-switch` en fresco | `{"engaged":false,"reason":null,…}` |
| SEG-02 | **PASS** | `POST /security/kill-switch {"engaged":true,"reason":"matriz: prueba de freno"}` **sin MFA enrolado** | 200 `{"ok":true,"engaged":true}`; el estado devuelve `reason`, `changed_by`, `changed_at` |
| SEG-03 | **PASS** | `POST /api/v1/chat` con el freno activo | **423** `{"code":"kill_switch_engaged","message":"El freno de emergencia está activo — libéralo desde Seguridad para enviar mensajes."}` |
| SEG-04 | **PASS** | `POST /security/kill-switch {"engaged":false,"totp":<válido>}` | 200 `{"ok":true,"engaged":false}`; el chat vuelve a admitir turnos (200) inmediatamente después |
| SEG-05 | **PASS** (código) | `frontend/src/components/KillSwitchBanner.tsx:51-54` | banner global con texto de alerta y `<Link to="/sistema?tab=seguridad">Liberar</Link>` |
| **Liberación SIN MFA (hallazgo C)** | **FALLA (parcial — sigue sin salida en instalación nueva)** | con MFA **no** enrolado: `POST … {"engaged":false}` y `{"engaged":false,"device_password":"contrasena-falsa-matriz"}` | el camino nuevo existe y llega de verdad al helper root (`security_api.py:117-166` → `hermes-tailscale-control` acción `kill_switch_release`), pero ambas respuestas son **403** `invalid_device_password` y el journal explica por qué: `WARNING hermes-tailscale-control: cuenta hermes-user sin contraseña válida (passwordless/locked) — gate FAIL-CLOSED (configura una en el onboarding)` + `kill_switch_release: PAM verification FAILED`. En una instalación nueva **nadie ha puesto contraseña de dispositivo**, así que la única salida real sigue siendo enrolar MFA con el freno echado (`POST /mfa/enroll` **sí** funciona frenado) y soltar con TOTP. Cero fugas: `grep` del journal por la contraseña falsa → 0 |
| SEG-06 | **[DUEÑO]** | `GET /api/v1/approvals/pending` | `[]` 200 — la tarjeta sólo la genera una propuesta de tool de un modelo real |
| SEG-07 | **[DUEÑO]** | idem | — |
| SEG-08 | **PASS** | `POST /api/v1/mfa/enroll {"totp":null}` (con el freno activo) | 200 `otpauth://totp/Safent:owner?secret=…&issuer=Safent&algorithm=SHA1&digits=6&period=30`; `GET /mfa/status` → `{"enrolled":true}`. Matiz: el enrolado es **inmediato** en la primera llamada (`approvals_api.py:82-92`) y la UI (`MfaEnroll.tsx`) sólo confirma en local — nadie comprueba que el dueño llegara a escanear el QR |
| SEG-09 | **PASS** | `POST /security/kill-switch {"engaged":false,"totp":"000000"}` | **401** `{"code":"invalid_totp"}` |
| SEG-10 | **PASS** | reenviar el TOTP ya gastado | **401** `{"code":"totp_replayed"}`; con un código nuevo → 200 |
| SEG-11 | **[DUEÑO]** | `GET /api/v1/inbound-delegations` | `[]` 200; exige un segundo Safent emparejado |
| SEG-12 | **PASS** | `POST /policies/preset` con `totp:""` y con TOTP válido | sin código **401** `invalid_totp`; con código 200 `{"ok":true,"preset":"bloqueado"}` y el catálogo pasa a **0 de 88 tools activas**; vuelta a `equilibrado` 200 |
| SEG-13 | **PASS** (código) | `SeguridadView.tsx:502-503,480-483` | los toggles se acumulan en `toolPending` y aparece la barra "Guardar cambios"/"Descartar" |
| SEG-14 | **PASS** | `POST /policies/tools {"tools":{"web_search":false},"totp":<válido>}` | 200 `{"ok":true,"count":1}`; `GET /policies` confirma `web_search:false` y persiste |
| SEG-15 | **FALLA (dead-end)** | `POST /policies/mfa_on_dangers {"enabled":false,"totp":<válido>}` → luego los **cuerpos exactos** que manda la UI cuando `mfaDisabled` (`SeguridadView.tsx:622-671`) | desactivar la verificación devuelve 200 `{"mfa_on_dangers":false}`, pero a partir de ahí **el backend sigue exigiendo TOTP** y la UI ya no lo pide: `POST /policies/preset {"preset":"permisivo","totp":""}` → **401**, `POST /policies/tools {…,"totp":""}` → **401**, y —lo peor— `POST /policies/mfa_on_dangers {"enabled":true,"totp":""}` → **401**: ni siquiera se puede volver a **encender** la verificación desde la UI. El dueño se queda con presets, lote de permisos y el propio interruptor rotos, con un toast de error y sin ningún sitio donde teclear el código |
| SEG-16 | **PASS** (código) | `seg.changes.discard` | vuelve al estado persistido sin llamada de red |
| SEG-17 | **PASS** (código) | `SeguridadView.tsx:517-539,854-861` | `defenseGroups` (categoría `security`) se pinta aparte del catálogo de capacidades |
| SEG-18 | **PASS** | `GET /api/v1/egress/mode` recién arrancado | `{"mode":"deny","description":"deny: only explicitly allowed domains reachable"}` — deny-by-default sigue cerrado |
| SEG-19 | **PASS** | `POST /egress/mode` con `totp:""` **teniendo `mfa_on_dangers:false`** y con TOTP | sin código **401** `Cambiar el modo de red exige tu código MFA` (no hay bypass, tal como dice el diseño); con código 200 `{"ok":true,"mode":"allow","pushed":true}` y vuelta a `deny` 200 |
| SEG-20 | **PASS** | `POST /egress/deny/add {"domain":"example.org"}` en modo `allow`, **sin** TOTP | 200 `{"ok":true,"denylist":["example.org"],"pushed":true}` |
| SEG-21 | **PASS** | `POST /egress/domains/grant` con dominio válido e inválido | `example.com` → 200 `{"domains":["example.com"],"pushed":true}`; `"no es dominio"` → **422** `{"code":"invalid_domain"}` (nada de `{ok:false}` bajo 200) |
| SEG-22 | **PASS** | `GET /api/v1/egress/domains` | `{"mode":"deny","domains":["example.com"],"denylist":[],"blocklist_count":0}` — el contador existe y viene del backend (0 en esta instalación) |
| SEG-23 | **PASS** | `GET /api/v1/tailnet` en fresco | `{"configured":false,"online":false,"node_name":null,…,"last_attempt":null}` |
| SEG-24 | **PASS — hallazgo D CERRADO** (éxito real: **[DUEÑO]**) | `POST /api/v1/tailnet/connect {"auth_key":"tskey-auth-FAKEmatrizfinal-noreal-…"}` y sondeo de `GET /tailnet` cada 15 s | 202 `{"staged":true}`; a los ~45 s el helper agota los reintentos y el estado pasa a `{"configured":false,"online":false,"last_attempt":{"at":"…14:47:42…","ok":false,"error_kind":"tailscale_up_failed"}}` — ya **no** miente con `configured:true`. La UI mapea ese estado a `failed` (`SeguridadView.tsx:1402-1406`). `grep` del journal por la clave falsa → **0**; `/run/hermes/tailscale-control/` vacío (shred) |
| SEG-25 | **PASS** (mecanismo) / **[DUEÑO]** (éxito) | `POST /api/v1/tailnet/disconnect {"password":"password-falsa-matriz-2"}` | 200 `{"staged":true}`; el helper root responde `disconnect action: PAM verification FAILED for user 'hermes-user' — aborting` y aborta sin tocar el marker; 0 apariciones de la contraseña en el journal |
| SEG-26 | **PASS** | `POST /api/v1/security/scans/install {"kind":"skill",…}` → `POST /api/v1/security/decisions {…,"decision":"allow","totp":<válido>}` | 201 `{"ok":true,"reauth_grant":"q7TOulZnKI3I…"}`; `GET /security/scans` deja la fila `official/research/gitnexus-explorer FAIL ALLOWED`; `GET /security/audit/head` con `integrity:"present"` |
| **SKL-04 (flujo de UI, UN solo TOTP)** | **PASS — hallazgo A CERRADO** | secuencia exacta de `SkillsView.handleScanApprove`: `scans/install` → `security/decisions {…,totp}` → `POST /skills/hub/install {"identifier":"official/research/gitnexus-explorer","force":true}` con cabecera `X-Owner-Reauth-Grant: <reauth_grant>` y **sin** segundo TOTP | **202** `{"op_id":…}` → `GET /skills/hub/ops/{id}` → `{"status":"done"}`; `GET /skills` lista `native:gitnexus-explorer`. El 401 `invalid_totp` de R11 ya no ocurre |
| SEG-WS | **PASS** | handshake WebSocket a `/api/v1/watch/agent/live` y `/api/v1/vnc` sin token, con token malo y con el bearer | sin token → **403**, token malo → **403**, bearer válido → **101 Switching Protocols** en ambos |
| SSH-01 | **PASS** | `podman exec … which ssh && ssh -V` | `/usr/bin/ssh`, `OpenSSH_9.6p1 Ubuntu-3ubuntu13.19` — el `openssh-client` del follow-up de `ssh-v2.md` ya está horneado |
| SSH-02 | **PASS — el GAP está cerrado** | dentro de la jaula: `build_capability_tool_specs(broker=…, consent_context=…)` y `GET /api/v1/policies` | el esquema de tools del LLM trae **24** capacidades e incluye `tailnet_ssh`, `tailnet_file_get`, `tailnet_file_put`; el catálogo de políticas (88 tools, preset `equilibrado`) también las lista. Ya no es cierto que "el agente no puede invocarlo" |
| SSH-03 | **PASS — el GAP está cerrado** | `GET /api/v1/tailnet/ssh-hosts`; `DELETE /api/v1/tailnet/ssh-hosts/{host}` | `{"hosts":[]}` 200; el DELETE valida el TOTP (`totp:""` → **422** `string_too_short`) y la UI lo consume (`client.ts:795-802`, `SshHostsSection`) |
