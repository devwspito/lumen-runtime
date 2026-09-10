# 025 — Matriz de capacidades EN VIVO (Safent 0.8.42)

Ejecutado el 2026-09-10 sobre `feat/safent-next` @ 34c463e, imagen local `ghcr.io/devwspito/safent:0.8.42` + companion `localhost/safent-ads:local`, podman rootful 4.9.3, instancia aislada `safent-repaso` (volumen `safent-repaso-data`, estado `/var/tmp/safent-repaso`, puerto `127.0.0.1:18081`), lanzada con `run-safent.sh`. Nada se marca PASS por documentación: cada fila cita el comando/llamada y lo observado. Auth de la API: handshake `GET /app/?k=<webui-bootstrap>` (leído de `/var/lib/hermes-bootstrap/bootstrap/webui-bootstrap`) → bearer estable inyectado en `index.html`. D-Bus como `hermes-user` con `busctl --system call org.hermes.Runtime /org/hermes/Runtime org.hermes.Runtime1 …`. Desmontaje completo al final (contenedor, volumen, proyecto compose `safent-ads` con `-v`, red `safent-companions`, `/var/tmp/safent-repaso`); `podman ps -a` sólo deja los `openshell-*` preexistentes.

## 1. Instalación y arranque — PASS

- Provisionado del companion 18 s (`ads-migrate` exit 0, `/mcp/health` → 401). `podman run` devolvió a 12:30:36; `Startup finished in 2.702s`; `hermes-runtime` Started 12:30:38; Uvicorn `:7517` 12:30:39 (+3 s); seeds excel/word/powerpoint conectadas 12:30:39–41; `companion_seed_imported` 12:30:39 y `safent-ads` con 61 tools `listo` a +5 s. `/healthz` 200 al primer sondeo.
- `systemctl is-system-running` = `running`, 0 units failed; `hermes-runtime/shell-server/egress-proxy/browser-netns/mcp-launcher/companion-egress` active; `hermes-landlock-assert` Result=success.
- D-Bus: `GetRuntimeStatus` responde como `hermes-user`; como root la introspección da `Access denied` (política sólo hermes/hermes-user; 146 miembros expuestos).
- Jaula, evidencia interna: PID1 `Seccomp: 2`; `hermes-runtime` uid 880, `Seccomp 2` (15 filtros), `CapBnd 0`, `NoNewPrivs 1`; `/sys/kernel/security/lsm` incluye `landlock`; netns `hermes-browser` y `hermes-mcp`; tablas nft `hermes_host`, `hermes_browser_egress`, `hermes_mcp_egress` (drop con log de RFC1918/CGNAT/loopback/metadata, `accept` sólo `10.201.0.10:8443` companion y proxy `:3128`); units MCP spawned con `User=hermes-sandbox`, `CapabilityBoundingSet=`, `NoNewPrivileges=yes`, `NetworkNamespacePath=/run/netns/hermes-mcp`.
- Logs 3 min: 0 `Traceback`; 46 líneas ERROR, todas ruido de Chromium (`GCM … DEPRECATED_ENDPOINT`, `CountResidentBytes: Operation not permitted`).
- Hallazgo: `podman restart/rm` nunca para en 10 s (`StopSignal (37) failed … SIGKILL`, 2/2).

## 2. Proveedores de modelo — FALLA (routing y SDKs), PASS parcial (Codex, Gemini)

- Out of the box: `GET /api/v1/providers` = `[]`, `providers/native/active` = `{}`. Ningún modelo configurado; el chat crea la tarea y el stream termina `outcome: failed`.
- Catálogo nativo (`/providers/native`): 51 ids — `nous` (device-code), `openai-codex`/`xai-oauth`/`qwen-oauth` (oauth_external), api_key para `openai-api`, `anthropic`, `gemini`, `zai` (GLM), `lmstudio`, `ollama-cloud`, `deepseek`… Kinds custom (`POST /providers`): 25 incl. `openrouter`, `vllm`, `ollama`, `lm_studio`, `llama_cpp`, `openai_codex`.
- Codex/ChatGPT: `POST /providers/openai-codex/oauth/start` → `{flow:"device_code", user_code:"59JC-NTLUW", verification_url:"https://auth.openai.com/codex/device", expires_in:900}`. PASS hasta el login (no completado: cuenta del dueño).
- Custom `anthropic` con clave placeholder: 201, `has_api_key:true`, `GET` redacta. `POST /{id}/test` → HTTP 200 `{"ok":false,"error":"Error code: 404"}` (opaco; con clave falsa se esperaría 401). Chat → `ImportError: The 'anthropic' package is required`. En la imagen sólo existe el SDK `openai`; faltan `anthropic`, `litellm`, `google.genai`, `zhipuai`.
- Nativos con placeholder: `anthropic`, `openai-api`, `zai` → mismo `ImportError` (heredaron `default_model: claude-sonnet-4-5` porque `ConfigureNativeProvider` sin `model` no fija uno propio). `gemini` → `"Gemini HTTP 400 … API key not valid"` pero `outcome: completed` (el error se pinta como respuesta del modelo). Tras configurar gemini, `openai-api gpt-4.1-mini`, `zai glm-4.5` y `anthropic` marcados activos siguen yendo a Gemini: `config.yaml` queda `model.provider: anthropic` y `.env` acumula `ANTHROPIC/OPENAI/GLM/GOOGLE_API_KEY`; el motor elige por entorno, no por el "activo" de la UI.

## 3. Herramientas dentro de la jaula — PASS (terminal enjaulado), FALTA (SSH)

- Catálogo (`GET /api/v1/policies`, preset `equilibrado`): 85 tools nativas. `true`: terminal, execute_code, process, read_file, write_file, patch, search_files, web_search, web_extract, browser_* (navigate/click/snapshot/vision/cdp…), image_generate, video_generate, text_to_speech, delegate_task, memory, skills_list, skill_view, send_message, computer_use, clarify. `skill_manage=false`. `cronjob_manage` y `setup_mcp` no existen en el catálogo (cron y MCP sólo desde la UI). Las tools MCP (excel 25…) no pasan por policies. `web-search/status`: brave/tavily/exa false, `ddgs_fallback: true`.
- Terminal: probado SIN modelo hablando con `/run/hermes/exec-launch.sock` (frame `>I`+JSON, SO_PEERCRED gid hermes) → `uid=886(hermes-sandbox)`, `CapBnd 0`, `NoNewPrivs 1`, `Seccomp 2`, netns propio, `master.key` y `hermes-home` `Permission denied`, `HTTPS_PROXY=http://10.200.0.1:3128`, `example.com` 200, `example.org` (denylist) 403, control plane 403. `ip` muere con `Bad system call` (SIGSYS del `SystemCallFilter`). Unidad transitoria con `ProtectSystem=strict`, `MemoryMax=512M`, `CPUQuota=50%`, `InaccessiblePaths` de claves/DB.
- Backends de hermes-agent 0.21.1 (`tools/environments/`): local, docker, ssh, modal, managed_modal, daytona, singularity, vercel_sandbox. Safent intercepta `CAGED_NATIVE_TOOLS` (terminal/process/execute_code/read/write/patch/search) y los ejecuta vía exec-launcher; el backend `ssh` de Hermes no se activa.
- SSH: `/usr/bin/ssh` (OpenSSH 9.6) existe, pero sin `~/.ssh`, sin DNS en la netns (`Could not resolve hostname github.com`), sin ruta a `:22`, sin verbo D-Bus/REST/UI, sin almacén de claves. Haría falta: tool `remote_shell(host, cmd)`; almacén de claves cifrado con `master.key` + `known_hosts` fijado; regla nft `daddr/dport 22` por host (o `ProxyCommand` vía CONNECT); tarjeta UI "Hosts remotos"; política HITL/MFA por host y clasificación DANGER de comandos.

## 4. Egress — PASS con hallazgo

- Dos planos. Browser/terminal (netns `hermes-browser`, proxy `10.200.0.1:3128`): `GET /egress/mode` = `allow` ("any domain reachable except denylist and system blocklist"); `example.com`/`example.org` 200 sin configurar nada — SECURITY.md promete default-deny. `POST /egress/deny/add example.org` → 403 inmediato. Modo `deny` (TOTP) → `example.net` bloqueado, `example.com` concedido 200; vuelta a `allow` OK.
- MCP (netns `hermes-mcp`, `10.200.1.1:3128`): default-deny (`example.com` 403); `POST /egress/mcp/domains/grant example.com` → 200, `example.net` sigue bloqueado.
- Sin proxy: `1.1.1.1:443` timeout; `169.254.169.254` y `10.201.0.1:8443` vía proxy 403; companion `10.201.0.10:8443` → 401 (única salida directa). Proxy `hermes.egress_proxy` como `hermes-egress`, política por socket UNIX, journal `decision/allow/deny`. El dueño añade dominios desde la vista `seguridad` o la API.

## 5. MCP — PASS (npx, managed-remote, borrado), FALLA (uvx nuevo)

- Seeds `healthy` a +5 s: excel 25, word 54, powerpoint 37, safent-ads 61 (`companion_status: listo`).
- Validación: `server_id` inválido 400; runner `bash` 400 (allowlist `npx/pipx/uvx`); slug managed-remote desconocido 400. Sólo `safent-control` y `safent-ads` admiten URL: no hay MCP remoto arbitrario por URL (diseño).
- stdio npx `@modelcontextprotocol/server-memory` online: scan PASS 97 → prefetch → 201, 9 tools, 6 s (journal dice 11).
- stdio uvx con paquete no cacheado (`mcp-server-time`, `mcp-server-fetch`): FALLA determinista `prefetch falló … Invalid cross-device link (os error 18)` al mover `uv-cache/.tmp*` → `archive-v0/`; `UV_LINK_MODE=copy` ya está puesto y no basta; `/var/lib/hermes` es un único ext4, `TMPDIR=/var/lib/hermes/tmp`; el mismo `uvx --help` en una unidad transitoria equivalente como `hermes` sí funciona. Con cache caliente (`mcp-server-time` tras calentar, `excel`) → 201. Offline puro no existe: el prefetch necesita PyPI/npm desde el daemon.
- `DELETE /mcp/{id}` 204, también para el seed `excel` (no resucita al recrear; re-añadido con su argv en 11 s).

## 6. Skills — PASS con hallazgo de seguridad

- `GET /skills/hub/search?q=git` devuelve resultados. Instalar `official/research/gitnexus-explorer` → bloqueado por scan `FAIL score 30` (privilege escalation, dropper…), HTTP 202 `blocked:true`. Con `force:true` → `op done` e instalado **sin TOTP**, pese a que `POST /security/decisions approve` había sido rechazado (`totp_replayed`): el override por Security Center exige MFA, el `force` del instalador sólo bearer.
- Tras instalar: `GET /skills` y D-Bus `ListSkills` lo listan (`native:gitnexus-explorer`, `surface_kinds: skill_manage`); `/skills/hub` `scan_verdict: caution`. El agente lo ve; `skill_manage=false` en el preset.

## 7. Cron y delegate_task — PASS (dispara/cancela), FALLA (UI)

- `POST /tasks/scheduled` `* * * * *` → 201 UUID. Journal: `triggers.timer.fired` 12:41:39 y 12:46:42, `tasks.loop.task_failed` (sin modelo válido). Con UUID: `POST /{id}/enabled false` 200, `DELETE` 204.
- `GET /tasks/configured` devuelve `trigger_id` corto de Neus (`c2217361b797`); detalle/toggle/delete exigen el UUID: desde la lista (CalendarView usa `task.trigger_id`) detalle 404 y toggle `{"ok":false,"error":"trigger_id inválido"}` con HTTP 200. `last_run_at/last_status` vacíos tras disparar; `/tasks/recent` siempre `[]`.
- `delegate_task` está en el catálogo; `SubmitInboundDelegation` exige firma (`missing_signature`) — no ejercitable sin un par emparejado.

## 8. Memoria/persistencia — PASS

`podman restart` → healthz a 13 s; mismo bearer (HKDF de `master.key`); sobreviven providers, nativo activo, MCP añadido, skill, cron (disabled), denylist/grants/modo, MFA, decisiones de scan. Igual tras recrear (§9), incluidas las eliminaciones.

## 9. Actualización — PASS (simulada)

CLI `cmd_update`: `_self_update` (raw GitHub, `_CLI_REV=8`) → `_ensure_agent` (launchd / `systemd --user`) → `_reclaim_space` (rmi de imágenes safent viejas + `image/builder prune`) → `podman pull` → `_run` (rm -f + run con el mismo volumen) → re-provision companion. UI: `POST /system/update` escribe `/var/lib/hermes/instance/.update-requested`, que `safent agent` en el host consume; sin agente, `updating:true` 15 min (`_FLAG_STALE_S`) y se limpia. Simulado con `run-safent.sh` misma imagen: healthy a 29 s, datos intactos. No ejecutado el `pull` real ni `_reclaim_space`.

## 10. Copias/exportación — FALTA

Ni CLI ni API tienen backup/export/restore; `GET /workspace/download` es un fichero del workspace (422 sin parámetro). Todo el estado vive en el volumen (`master.key`, `shell-state.db`, `hermes-home`, `skills`, `mcp-installs`) más `~/.safent/companions/ads` (TLS, bearer, secretos): sólo `podman volume export` a mano.

## 11. Seguridad visible — PASS (MFA), FALTA (kill switch en UI), NO PROBABLE (HITL)

- MFA: `POST /mfa/enroll` → `otpauth_uri`; TOTP exigido en `policies/tool|preset|mfa_on_dangers`, `egress/mode`, `security/decisions approve`; código malo 401 `invalid_totp`; reutilizado 401 `totp_replayed`.
- Tarjeta HITL: `approvals/pending=[]`; sólo la genera una propuesta de tool del modelo. `Approve/Reject/ResolveApproval` existen en D-Bus.
- Kill switch: D-Bus `Pause("…")` → `true`, `Resume` → `true`, pero `GetRuntimeStatus` sigue `idle` (no expone `paused`), sin ruta REST ni botón (el "Pausar" de i18n es de tareas). Alternativas: `enabled=false` por trigger; preset `bloqueado` (TOTP).

## 12. UI — PASS con dos dead-ends

`/` 307 → `/app/`; 19 rutas SPA 200 (chat, proveedores, mcp, skills, seguridad, programadas, ajustes, sistema, agentes, anuncios, capacidades, ensenar, en-vivo, office, integraciones, memoria, archivos, coste); CE expone 10 `views`. No hay `/login`: sin `?k=` la página carga y toda mutación es 401 (`client.ts` reintenta `session/refresh`). 25 GET que usa el frontend: todos 200 salvo `GET /agents/active` → 405 (sólo existe `POST /{id}/activate`; `getActiveAgent` lo silencia con `.catch` → la UI nunca conoce el agente activo). Más el dead-end de cron (§7). Sin bearer: `POST /egress/deny/add` 401.

## 13. Enterprise — PASS (inerte), NO PROBABLE (pairing)

`instance/status`: `community`, `associated:false`; `hermes-config-sync.service` inactive por `ConditionPathExists=/var/lib/hermes/instance/.enterprise` (`.path` esperando). `POST /instance/pair {code}` → 400 "Error de red al contactar el control plane" en 0,2 s: `cloud.safent.run` no resuelve desde el contenedor. Requiere código de tenant y control plane alcanzable.

## 14. Distribución — PASS (Linux rootful), NO PROBABLE (Mac/Windows/rootless)

Supuestos del CLI (`safent`, POSIX sh; `safent.ps1`): podman o docker en PATH; macOS → `podman machine` rootful (auto `init --rootful --cpus 4 --memory 8192 --disk-size 60`), sólo Apple Silicon según `INSTALL-mac.md`, que además **construye** la imagen (15-20 min) en vez de hacer pull; Windows → WSL2 kernel ≥ 6.6 (Landlock) + Podman Desktop; Linux → el podman del usuario (rootless por defecto, sin sudo) aunque toda verificación es rootful. `run-safent.sh` exige bash, perfil seccomp, Landlock, `--systemd=always` y `--security-opt unmask` (podman-only), y añade `apparmor=unconfined` si AppArmor está activo. Docker: README dice "Podman o Docker Desktop", pero `docker run --help` en este host no admite `--systemd`/`unmask` y `safent.ps1`/`INSTALL-win.md` lo declaran imposible. Nombres distintos: `SAFENT_DATA_VOLUME` (CLI) vs `SAFENT_VOLUME` (`run-safent.sh`).

## Top 10 hallazgos (por impacto)

1. El proveedor "activo" no gobierna el motor: todo va a Gemini una vez configurada. Fix: `SetActiveProvider/ConfigureNativeProvider` escriben `model.provider`+`model` y priorizan `.env`; test de routing. **M**
2. Falta el SDK `anthropic` (y otros): Anthropic/OpenAI-API/GLM nativos y custom mueren con `ImportError`. Fix: añadir `anthropic>=0.39` al Containerfile o gatear el catálogo por SDK presente. **S**
3. Egress browser/terminal `allow` por defecto contra SECURITY.md. Fix: default `deny` en instalación nueva (o corregir doc + aviso en UI). **S**
4. `force:true` en `/skills/hub/install` salta un scan FAIL sin MFA. Fix: `require_owner_mfa` como en `/security/decisions`. **S**
5. uvx con paquete nuevo → EXDEV: ningún MCP Python nuevo instalable. Fix: prefetch en unidad transitoria (como el launcher) o `uv tool install --no-cache`; test con paquete no cacheado. **M**
6. Cron: ids incompatibles lista/detalle y `recent` vacío. Fix: `list_configured_tasks` devuelve el UUID y el loop alimenta `/tasks/recent`. **M**
7. `GET /agents/active` 405. Fix: ruta GET sobre `GetActiveAgent`. **S**
8. Kill switch sin UI ni estado. Fix: `POST /runtime/pause|resume`, `paused` en status, botón en Seguridad. **M**
9. Sin backup/restore. Fix: `safent backup|restore` (volumen + estado del companion). **M**
10. Docs de plataforma contradictorias (Docker sí/no, rootless sin verificar, INSTALL-mac construye) y `SAFENT_DATA_VOLUME`≠`SAFENT_VOLUME`. Fix: matriz de soporte única y alinear nombres. **S**

Menores: error de Gemini como respuesta `completed`; `/test` 200 con `"Error code: 404"`; modelo heredado al configurar nativos; parada que acaba en SIGKILL.

## Lo que no pude probar y por qué

Tarjeta HITL real, image/video/tts/web_search en ejecución (requieren un modelo funcional); login Codex completo (cuenta ChatGPT del dueño); pairing (código de tenant, `cloud.safent.run` no resuelve); Mac/Windows/rootless (sin máquina; rootless exigiría copiar 7,8 GB a otro storage); `safent update` real (pull de ghcr y `_reclaim_space` tocan imágenes del host); `delegate_task` entre instancias (firma); instalación offline (habría que cortar la red del daemon); SSH (no existe mecanismo).
