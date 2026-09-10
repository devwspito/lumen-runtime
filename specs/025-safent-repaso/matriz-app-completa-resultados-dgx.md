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
