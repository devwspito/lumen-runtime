# 025 — Matriz de verificación de release · imagen FINAL `39eeb8e`

Ejecutada el **2026-09-10** sobre la DGX (`linux/aarch64`, podman **rootless** 4.9.3, uid 1000)
contra `localhost/safent-runtime:latest` == `:0.8.42`, label
`org.opencontainers.image.revision=39eeb8e` — la imagen que contiene el repaso 025 entero, el
lado de runtime de 026, los contratos 028 T004-T006 y R1b T003/T015-T017.

Instancia **única** `matriz-final-2` (volumen `matriz-final-2-data`, `127.0.0.1:18095`, estado
propio bajo el scratchpad de la sesión, **nunca** `~/.safent`), lanzada con:

```sh
SAFENT_NAME=matriz-final-2 SAFENT_STATE=$SCRATCH/state SAFENT_STATE_HOME=$SCRATCH/state \
SAFENT_COMPANION_STATE=$SCRATCH/state/companions/ads \
./ops/container/run-safent.sh localhost/safent-runtime:latest 18095 --no-companion
```

Los verbos del CLI del anfitrión se ejecutaron desde el repo con
`SAFENT_NAME=matriz-final-2 SAFENT_STATE_HOME=$SCRATCH/state SAFENT_PORT=18095
SAFENT_DATA_VOLUME=matriz-final-2-data SAFENT_IMAGE=localhost/safent-runtime:latest
SAFENT_PODMAN=/usr/bin/podman`.

**Este host tiene una pila de companion AJENA** (`safent-ads-ads-{api,db,worker,broker,migrate}-1`,
red `safent-companions`) y un `safent-demo` de otra sesión. Nada de eso se tocó: la pasada corrió
con `--no-companion` y con `SAFENT_STATE_HOME` propio, y `safent facts` lo confirma
(`companionScaffold:false`, `companionContainers:{running:0,total:0}`). **Las filas que dependen
del companion son N-A aquí** — están probadas en el Mac.

Auth: `GET /app/?k=$(podman exec matriz-final-2 cat
/var/lib/hermes-bootstrap/bootstrap/webui-bootstrap)` → bearer inyectado en
`window.__SAFENT_TOKEN__`. **Ninguna credencial real** en toda la pasada (claves de proveedor,
Brave, MFA: sólo valores falsos, y el secreto TOTP murió con el volumen).

**Leyenda:** `PASS` · `FALLA` · `N-A` (no aplicable en esta máquina, con motivo).

---

## §A — Arranque, salud y frontera de autenticación

| id | resultado | cómo (comando exacto) | evidencia |
|---|---|---|---|
| A-01 | **PASS** | `./ops/container/run-safent.sh localhost/safent-runtime:latest 18095 --no-companion` + `curl -o /dev/null -w '%{http_code}' http://127.0.0.1:18095/healthz` cada 2 s | `podman run` devolvió 22:26:55; `/healthz` **200 al primer sondeo** (22:27:02, +7 s). `systemd-analyze` → `Startup finished in 1.188s (userspace)` |
| A-02 | **PASS** | `podman exec matriz-final-2 systemctl is-system-running` / `systemctl --failed --no-legend --plain` | `running`; **0 unidades failed** (salida vacía), verificado al arrancar y de nuevo al terminar la pasada. 20 unidades `hermes-*` activas |
| A-03 | **PASS** | `podman exec matriz-final-2 systemctl show hermes-landlock-assert -p Result -p ExecMainStatus`; `journalctl \| grep landlock_loader.applied` | `Result=success`, `ExecMainStatus=0`; `hermes-landlock-assert: Landlock LSM activo — OK`; `landlock_loader.applied abi=7 capability=runtime rules=13 mask=0x77bf` frente a `capability=browser rules=20 mask=0x57bf` → **REFER (bit 13, 0x2000) sólo en RUNTIME**, exactamente `a54105d`. `runtime_landlock.applied outcome=applied enforcing=True`, `confinement_check.PASS` |
| A-04 | **PASS** | `curl "http://127.0.0.1:18095/app/?k=$(podman exec matriz-final-2 cat /var/lib/hermes-bootstrap/bootstrap/webui-bootstrap)"` | 200 y `__SAFENT_TOKEN__="1a647871…"` (64 hex) inyectado en el `index.html`; ese bearer sirvió para todas las llamadas posteriores (`GET /api/v1/agents` → 200) |
| A-05 | **PASS** | los **71** `GET /api/v1/*` extraídos de `/openapi.json`, en bucle `curl -o /dev/null -w '%{http_code}'` **sin cabecera** (params de ruta sustituidos por `x`) | **71/71 → 401**, cero excepciones. Con bearer, muestreo de 8 rutas (`/agents`, `/mcp`, `/policies`, `/system/update`, `/system/requests`, `/security/kill-switch`, `/egress/mode`, `/instance/features`) → 200 |
| A-06 | **PASS** | `curl http://127.0.0.1:18095/app/` ×5 **sin `?k=`**, y después `journalctl -u hermes-shell-server` | HTML servido 200 **sin ninguna línea `__SAFENT_TOKEN__`** (`diff` contra la versión con `?k=` sólo muestra esa línea de más). El log del shell-server registra exactamente **5 `GET /app/` y CERO `/api/v1/*` posteriores**: la tormenta desapareció. El bundle horneado lleva la pantalla (`grep /opt/safent-webapp/assets/*.js` → `reconnect.title`, `reconnect.reason.no_token`, `reconnect.reason.refresh_failed`, `unauthenticated`×2). Prueba de comportamiento real del SPA: `cd frontend && npx vitest run src/App.test.tsx` → **2/2 passed** (`issues ZERO /api/v1/* requests and shows the reconnect screen when no bearer exists at all` + `a stale bearer costs exactly ONE refresh attempt, then the reconnect screen — no request storm`) |
| A-07 | **FALLA (menor, informativa)** | `curl -o /dev/null -w '%{http_code}' http://127.0.0.1:18095/openapi.json` **sin bearer** | **200**: el esquema completo (71 rutas GET + todo el resto) se sirve sin autenticar mientras cada `/api/v1/*` da 401. No filtra datos, pero regala el mapa entero de la superficie a quien alcance el puerto |

## §B — Las 11 FALLA de la matriz anterior, reejecutadas

| id | resultado | cómo (comando exacto) | evidencia |
|---|---|---|---|
| MCP-04 | **PASS — EXDEV CERRADO** | `POST /api/v1/mcp {"server_id":"time","argv":["uvx","mcp-server-time"]}` sobre volumen nuevo | **201 en 2,5 s** `{"ok":true,"tool_count":2}`. Precondición verificada: `mcp-server-time` y `tzlocal` **NO** están en la caché horneada (`ls /opt/hermes/mcp-seed/uv-cache/wheels-v6/pypi/` → 100 paquetes, ninguno de los dos; el Containerfile sólo calienta excel/word/powerpoint + mcp-remote). Aparecieron en el volumen a las **22:32:53**, durante esta instalación: `/var/lib/hermes/uv-cache/archive-v0/QXA28V8en0zBzxbA/tzlocal-5.4.4.dist-info`. El `rename` a `archive-v0/` que antes moría con `Invalid cross-device link (os error 18)` **ahora sale bien** — es el efecto de A-03 (REFER en RUNTIME) |
| MCP-05 | **PASS** | `POST /api/v1/mcp {"server_id":"time-brave","argv":["uvx","mcp-server-time"],"env":{"BRAVE_API_KEY":"br-FALSA-matriz-no-real"}}` (el ejemplo literal del formulario, `McpView.tsx:1148`) | **201** `{"ok":true,"tool_count":2}`. Quien copie el placeholder de la UI ya no recibe un 400 crudo. `GET /api/v1/mcp` → **0 coincidencias** del valor en claro |
| SEG-15 | **PASS — dead-end CERRADO** | ida y vuelta completa: `POST /policies/mfa_on_dangers {"enabled":false,"totp":<válido>}` → `POST /policies/tools {"tools":{"web_search":false},"totp":""}` → `POST /policies/preset {"preset":"permisivo","totp":""}` → `POST /policies/mfa_on_dangers {"enabled":true,"totp":<fresco>}` | Apagar → 200 `{"mfa_on_dangers":false}`. Con el interruptor apagado, `tools` → **200 `{"ok":true,"count":1}`** y `preset` → **200** (antes ambos 401). Volver a encender con TOTP → **200 `{"mfa_on_dangers":true}`**. El arreglo está en `frontend/src/views/SeguridadView.tsx:664-685`: `requestMfaDangersToggle` ramifica por **enrolado de MFA**, no por el valor del interruptor, así que abre el `MfaModal` y el dueño puede teclear el código. (Con el cuerpo VIEJO `{"enabled":true,"totp":""}` el backend sigue —correctamente— devolviendo 401; ese cuerpo ya no lo manda nadie) |
| PROV-02 | **PASS** | cuerpo actual de la UI (`ProvidersView.tsx:545-549`, `{provider_id, api_key, model}` con `model` precargado del catálogo) → `POST /providers/anthropic/activate` → `cat config.yaml` | `GET /providers/native` trae `default_model` por proveedor (`anthropic → claude-sonnet-4-6`, `gemini → gemini-2.5-flash`, `openai-api → gpt-5.4-nano`), así que el campo llega relleno. Tras activar, `config.yaml` = `model: {'provider': 'anthropic', 'default': 'claude-sonnet-4-5'}` — **con `model.default`**. El `HermesModelNotConfiguredError` del primer turno desapareció |
| PROV-03 | **FALLA (parcial — cambia la causa, no el síntoma)** | `POST /api/v1/providers/anthropic/test` (id nativo, lo que manda `testProvider(created.provider_id)`) | El bug de `UUID(provider_id)` **está arreglado**: `test_provider` deriva a `_test_native_provider` (`dbus_runtime_service.py:1157-1159`) y ya **no** responde `daemon_unavailable`. Prueba positiva con **gemini**: `{"ok":false,"error":"Error code: 400 - …'Please pass a valid API key'…"}` — error REAL del proveedor. **Pero con anthropic** la sonda sale a `POST https://api.anthropic.com/chat/completions` (journal literal) → **404 siempre**, con clave válida o no: `api.anthropic.com` no sirve `/chat/completions` (su ruta es `/v1/messages`). Como `ProvidersView.tsx:554-568` activa **sólo si `r?.ok === true`**, la tarjeta de Anthropic sigue cayendo en `addConnFailed` y **nunca se auto-activa**. Además `{ok:false}` viaja bajo **HTTP 200** |
| PROV-05 | **PASS — la ventana de ~30 s CERRADA** | con `anthropic` activo: `POST /providers/gemini/activate` y **el turno en el mismo segundo** `POST /api/v1/chat`, mirando el journal | Activación **22:35:54**, `hermes.dbus.native_provider_reactivated id=gemini` 22:35:54; el turno lo resolvió `hermes.nous_engine.native_provider_resolved provider=gemini model=gemini-2.5-flash` y la llamada salió a `generativelanguage.googleapis.com/v1beta` (`Gemini HTTP 400 … API key not valid`, esperado con clave falsa). **El proveedor nuevo gobierna el turno inmediatamente siguiente**, sin reinicio |
| MEM-06 | **PASS** | `curl -X DELETE /api/v1/memory/noexiste:0` | **404** `{"code":"not_found","message":"memory entry not found"}` (antes 200). `GET` del mismo id → 404: los dos verbos ya coinciden |
| BKP-01 | **PASS — CERRADO** | `./safent --no-companion backup $SCRATCH/out/backups` y después `SAFENT_NAME=matriz-final-3 SAFENT_PORT=18096 SAFENT_DATA_VOLUME=matriz-final-3-data ./safent --no-companion restore <archivo>` | Backup: para, exporta y rearranca; `safent-backup-20260910T204037Z.tar.gz` de **154 MB** en 20 s, `0600`. Restore en **8,7 s** y —lo que fallaba— `podman ps -a` **sí** lista `matriz-final-3 \| Up 1 second \| 127.0.0.1:18096->7517/tcp`. `/healthz` 200 al primer sondeo; **todo el estado viaja**: mismo bearer (el de `:18095` autentica en `:18096` → 200), proveedor activo `gemini/gemini-2.5-flash`, `mfa.enrolled:true`, preset `equilibrado`, los MCP de oficina. Restore repetido sin `--force` → `[x] Volume 'matriz-final-3-data' already exists — refusing to overwrite it.` Instancia destruida acto seguido |
| R17 | **PASS — salida soberana real** | freno echado sin MFA → `POST /security/kill-switch {"engaged":false}` → `./safent brake release` | Por REST sin contraseña de dispositivo sigue **403 `invalid_device_password`** (fail-closed, correcto en instalación nueva). El CLI del anfitrión **sí** libera: `[ok] Emergency brake released (sovereign host CLI…)`, `GET /security/kill-switch` → `engaged:false`, y `POST /api/v1/chat` vuelve a **200**. El dead-end «instancia frenada sin salida» está cerrado (la parte de auditoría, no — ver CLI-N4) |
| ADS-02 | **N-A** | requiere el companion `safent-ads` arriba (clave SSO `ads-sso.key` legible por `User=hermes`) | Esta máquina tiene una pila `safent-ads-ads-*` **ajena** y una red `safent-companions` ajena; levantar o reaprovisionar un companion propio las tocaría. La pasada corrió `--no-companion`. Probado en el Mac. Lo que **sí** se pudo comprobar aquí sin companion: `POST /api/v1/ads/bridge/session` → `{"status":"unavailable","reason":"not_installed"}` (ADS-N1) y `ReloadCompanionPresence` → `{"ok": false, "reason": "not_installed"}` — estado honesto, ni `ready` falso ni contagio de la pila ajena |
| CLI-08 | **N-A (causa raíz cerrada en código)** | ejecutar `./safent companion status` interrogaría el proyecto compose `safent-ads` **ajeno** | `_companion_container_counts()` (`safent:1038-1048`) llama a **`_companion_env` en la línea 1041**, antes de `compose ps -q -a` — es exactamente la llamada que faltaba y que hacía fallar la interpolación (`required variable ADS_POSTGRES_PASSWORD is missing a value`) y devolver `0/0`. No ejecutado en la DGX |
| CLI-10 | **N-A (causa raíz cerrada en código)** | `./safent companion rotate` recrearía los contenedores del companion **ajeno** | `_companion_env()` (`safent:947`) ahora fija `SAFENT_ADS_IMAGE="$(_persisted_ads_image)"` — «ALWAYS the persisted ref (never an ambient env var)», comentario en `safent:936`. El `ghcr.io/devwspito/safent-ads:latest` cableado que mezclaba imágenes y reventaba alembic sólo sobrevive como último recurso de `_companion_update_env` (`safent:968`), tras consultar `$COMPANION_STATE/image`. No ejecutado en la DGX |

## §C — Superficies nuevas (028 T004-T006 · R1b T003/T015-T017 · 026)

| id | resultado | cómo (comando exacto) | evidencia |
|---|---|---|---|
| UPD-N1 | **PASS (fail-closed)** | `GET /api/v1/system/update` con bearer; y dentro de la jaula `podman exec -u hermes matriz-final-2 python3 -c "from hermes.shell_server.runtime_manifest import fetch_verified_manifest, is_placeholder_pubkey, _usable_pubkey_text; …"` | Respuesta: `{"current_version":"0.8.42","latest_version":"0.8.42","update_available":false,"updating":false,"engine_digest":null,"companion_digest":null,"pieces":[]}`. La clave horneada `/usr/share/hermes/keys/runtime-manifest.pub` es la **REAL** (`RWT9keHyuQgoJG28L+t7mV7…`), `is_placeholder_pubkey → False`. `fetch_verified_manifest() → None` (no hay manifiesto firmado publicado) y por tanto, **forzando `latest=9.9.9` contra `current=0.8.42`, `update_available` sigue siendo `False`**: sin manifiesto verificado no hay botón, pase lo que pase con el `VERSION` en claro (`system_update.py:76`) |
| UPD-N2 | **FALLA (menor, desviación de contrato)** | mismo `GET /api/v1/system/update` contra `contracts/update.md §3` | El contrato dice que la ruta «se amplía con los mismos campos» del objeto `__safentUpdate` (`available`, `current: VersionSet`, `to`, `pieces`, `checked_at`) **conservando** los tres viejos. Sólo se añadieron `pieces`, `engine_digest` y `companion_digest`: **faltan `available`, `current` (como VersionSet), `to` y `checked_at`**. La UI funciona porque `SystemUpdateFooter.tsx:62-73` sigue leyendo `update_available`/`latest_version`, pero el contrato escrito y la ruta no coinciden. No hay `is_newer` en ninguna parte: su semántica vive en `update_available` |
| REQ-01 | **PASS** | `POST /api/v1/system/requests {"verb":"install_companion","slug":"safent-ads"}` | 200 `{"accepted":true,"request":{"verb":"install_companion","state":"pending","expires_at":"2026-09-10T20:59:39Z"}}` |
| REQ-02 | **PASS** | `… {"verb":"repair_companion","slug":"safent-ads"}` | 200 `pending`, `expires_at` 20:44:39 |
| REQ-03 | **PASS** | `… {"verb":"update_system"}` | 200 `pending`, `expires_at` 21:14:39 |
| REQ-04 | **PASS** | `… {"verb":"remove_companion","slug":"safent-ads","retention":"purge"}` y `… {"verb":"uninstall_system"}` | 200 `pending` en ambos (20:34:53 y 20:39:53) |
| REQ-05 | **N-A / PASS por contrato** | `… {"verb":"restart"}` | **400 `{"accepted":false,"code":"unknown_verb"}`**. `restart` **no** pertenece al vocabulario cerrado: `contracts/install-request.md §1` define exactamente cinco `HostVerb` (`install_companion`, `repair_companion`, `remove_companion`, `update_system`, `uninstall_system`). El rechazo es el comportamiento correcto; lo que falla es la expectativa, no el producto |
| REQ-06 | **PASS** | `… {"verb":"rm_rf_slash"}` y `… {"verb":"install_companion","slug":"evil"}` | **400 `unknown_verb`** y **400 `unknown_slug`**. Ningún fichero escrito por un valor desconocido |
| REQ-07 | **PASS** | repetir `{"verb":"update_system"}` con una marca viva | **409** `{"accepted":false,"request":{…"state":"pending"…}}` — la segunda pulsación devuelve la existente, no lanza una segunda instalación (029 FR-008) |
| REQ-08 | **PASS** | aritmética sobre `created_at`/`expires_at` de las cinco marcas | 30 / 15 / 5 / 45 / 10 min para install / repair / remove / update / uninstall — **exactamente** la tabla de `install-request.md §2`. El `_FLAG_STALE_S` único de 15 min ya no manda |
| REQ-09 | **PASS** | `GET /api/v1/system/requests` | 200 con las **cinco** marcas, cada una con `verb`, `state:"pending"` y `expires_at` |
| REQ-10 | **PASS** | `ls -la /var/lib/hermes/instance/` dentro; `podman volume inspect matriz-final-2-data --format '{{.Mountpoint}}'` + `podman unshare cat …/instance/request-*.json` desde el **host** | Los cinco `request-<verbo>.json` aparecen en el directorio **visible desde el host** (`~/.local/share/containers/storage/volumes/matriz-final-2-data/_data/instance/`), modo **0600**. Contenido literal: `{"schema_version": 1, "verb": "install_companion", "created_at": …, "expires_at": …, "attempt": 1, "slug": "safent-ads"}` — **ningún campo lleva orden, ruta, URL, imagen ni argumento**; `retention` sólo en `remove_companion`. Se conservan además los alias `.update-requested` / `.uninstall-requested` (expandir→contraer) y `GET /system/update` pasa a `updating:true` mientras la marca vive |
| CLI-N1 | **PASS** | `./safent facts --json` | **UNA** línea en stdout, JSON parseable, **19 claves, 0 en snake_case**: `{"os":"linux","arch":"arm64","freeDiskBytes":…,"runtimeStaged":false,"machines":[],"engineContainer":{"exists":true,"running":true,"imageDigest":"c0b9f0b0e62e…"},"publishedPort":18095,"dataVolume":true,"companionScaffold":false,"companionContainers":{"running":0,"total":0},"daemonHealth":"healthy","appVersion":"0.8.42","userNsAllowed":true,…}`. **`engineContainer.running=true`**, `publishedPort` resuelto al puerto real y `companionContainers 0/0` (no contamina con la pila ajena). stderr vacío |
| CLI-N2 | **FALLA** | `./safent --porcelain status >out 2>err` | stdout trae **`[ok] Safent running at  http://localhost:18095/   (open with: safent)`** — texto humano, no NDJSON. Viola `contracts/app-engine.md §2` («stdout — exclusivamente NDJSON… Nada más se escribe aquí»). Causa: `cmd_status()` (`safent:788-796`) hace `echo` incondicional y nunca mira `$PORCELAIN`; la bandera se consume en `safent:1825` pero sólo la honran los verbos T004 (`facts`, `stage-runtime`, `ensure-machine`, `ensure-images`, `up`, `companion install\|repair` — líneas 1487/1493/1503/1516/1647/1759). Mismo patrón en `cmd_url()` (`safent:379-383`), que imprime en stdout la URL **con el vale `?k=`** también bajo `--porcelain`, contra `app-engine.md §5` («Nunca en stdout»). Atenuante: ni `status` ni `url` están en la tabla de verbos de §4, así que ningún camino de la app se rompe hoy; un envoltorio que los invocase sintetizaría `cli_porcelain_unsupported`. Sin fuga de vale en la salida de `status` (0 coincidencias de `?k=`/64-hex) |
| CLI-N3 | **PASS** | `./safent brake release` con el freno echado | `[ok] Emergency brake released (sovereign host CLI, audited as the owner, reason="host_cli").`, rc=0; `GET /security/kill-switch` → `{"engaged":false,…}`; journal `hermes.dbus.agent_resumed` |
| CLI-N4 | **FALLA** | tras CLI-N3: `GET /api/v1/audit?limit=60` y `podman exec matriz-final-2 python3 -c "sqlite3 … select audit_kind,count(*) from audit_chain_entries group by 1"` | **La entrada auditada no existe.** Repetido dos veces (freno+liberación a las 22:31 y a las 22:41): la cadena firmada crece con `task_claimed`/`task_failed`/`task_completed`/`chat_replied` (9 entradas) pero contiene **0 `AGENT_RESUMED` y 0 `AGENT_PAUSED`**, y `select count(*) … where payload_json\|\|description like '%host_cli%'` → **0**. `/api/v1/audit` tampoco los muestra. Causa raíz: `src/hermes/runtime/__main__.py:1427` construye `SqliteAgentState(db_path=db_path)` **sin `signer=` ni `audit_repo=`**, y `_emit_audit_resumed` (`sqlite_agent_state.py:150-163`) hace `if self._signer is None or self._audit_repo is None: return` — el `reason="host_cli"` que `8907b94` hila desde `brake_release_cli.py:63` hasta el `payload` se calcula y **se tira**. El mensaje del CLI («audited as the owner») afirma algo que no ocurre: una liberación soberana sin MFA es hoy **indistinguible e invisible** en la cadena firmada (CWE-778) |
| SEG-N1 | **PASS** | `POST /security/kill-switch {"engaged":true,"reason":"matriz-final: freno de prueba"}` → `POST /api/v1/chat` → `./safent brake release` → `POST /api/v1/chat` | Echar sin MFA → 200 `{"ok":true,"engaged":true}`; el estado devuelve `reason`, `changed_by`, `changed_at`. Chat con el freno → **423** `{"code":"kill_switch_engaged","message":"El freno de emergencia está activo — libéralo desde Seguridad para enviar mensajes."}`. Tras liberar, chat → **200** |
| WS-01 | **PASS** | handshake `Upgrade: websocket` a `/api/v1/vnc` sin token, con `?token=deadbeef` y con el bearer | **403 / 403 / 101 Switching Protocols** |
| WS-02 | **PASS** | idem contra `/api/v1/watch/agent/live` | **403 / 403 / 101 Switching Protocols** |
| MCP-N1 | **PASS** | ver MCP-05 (`BRAVE_API_KEY` aceptada) | 201 |
| MCP-N2 | **PASS** | `POST /api/v1/mcp` con `env` `{"LD_PRELOAD":"/tmp/x.so"}`, `{"NODE_OPTIONS":"--require /tmp/x.js"}`, `{"GIT_SSH_COMMAND":"sh -c id"}` | **400 en los tres**: `env inválido: clave de env no permitida: 'LD_PRELOAD' (reservada por el sistema)` (ídem `NODE_OPTIONS`, `GIT_SSH_COMMAND`). Deny-list BYOK compartida de `23e2eec` en su sitio |
| MEM-N1 | **PASS** | ver MEM-06 | 404 |
| DBUS-01 | **PASS** | `podman exec -u 1000 matriz-final-2 busctl --system call org.hermes.Runtime /org/hermes/Runtime org.hermes.Runtime1 ReloadCompanionPresence s "safent-ads"` y lo mismo `-u hermes` | uid **1000** (= `hermes-user`, verificado con `id hermes-user`) → `Call failed: UID 1000 no autorizado para 'reload_companion_presence' (solo el uid del shell-server, contracts/sso.md §3, CWE-862)`. uid **hermes (880)** → aceptado: `s "{\"ok\": false, \"reason\": \"not_installed\"}"`. La frontera de T017 se sostiene y el estado es honesto |
| ADS-N1 | **PASS** | `POST /api/v1/ads/bridge/session` (bearer) | **200** `{"status":"unavailable","reason":"not_installed"}` — la entrada «Anuncios» del sidebar resuelve a `not_installed`, no a un `ready` falso, y no ve la pila `safent-ads-*` ajena del host |

## §Menores anotados

- **El preset pisa el interruptor de MFA sin avisar.** Con `mfa_on_dangers:false`, `POST /policies/preset {"preset":"bloqueado","totp":""}` → 200 y `GET /policies` pasa a `mfa_on_dangers:true`. Reproducido con `permisivo`, `equilibrado` y `bloqueado`. Yerra del lado seguro, pero borra una decisión explícita del dueño en silencio.
- **`POST /providers/native` sigue admitiendo alta sin modelo:** `{"provider_id":"anthropic","api_key":"…"}` → **201 `{"ok":true}`**, y `{"…","set_active":true}` sin `model` → **201 `{"ok":false,"error":"model requerido para activar"}`** (`{ok:false}` bajo 2xx). Ya no rompe nada porque la UI precarga `default_model`, pero el backend no valida (no hay 422).
- **`{ok:false}` bajo HTTP 200 en `POST /providers/{id}/test`** — el anti-patrón sigue, ahora con el error real del proveedor dentro.
- **La caché uv/npm viaja en la imagen** (`/opt/hermes/mcp-seed`, copiada al volumen en el arranque): cualquier fila que exija «paquete nunca cacheado» debe elegir un paquete fuera de esa lista de 100 (comprobado antes de dar MCP-04 por bueno).

## §Desmontaje

`podman rm -f matriz-final-3` · `podman volume rm matriz-final-3-data` (tras BKP-01) ·
`podman rm -f matriz-final-2` · `podman volume rm matriz-final-2-data` · borrado del estado
`$SCRATCH/state`, `$SCRATCH/state3` y del archivo de backup.

Integridad del entorno ajeno verificada al terminar: los cinco `safent-ads-ads-*` siguen `Up`
(`ads-db`/`ads-api` `healthy`), `safent-demo` `Up`, los tres `safent-{diag,v839,audit}` intactos,
`podman network ls` → `podman` + `safent-companions` (sin crear ni borrar ninguna), y `~/.safent`
no se tocó en toda la pasada.

---

## Resumen

**52 filas · 45 PASS · 4 FALLA · 3 N-A.**

### Las 4 FALLA

1. **CLI-N4 — la liberación soberana del freno no deja rastro auditado.** `safent brake release`
   dice «audited as the owner, reason="host_cli"» y la cadena firmada no contiene **ninguna**
   entrada `AGENT_RESUMED`/`AGENT_PAUSED` (0 de 9). Root cause: `runtime/__main__.py:1427`
   instancia `SqliteAgentState(db_path=db_path)` sin `signer`/`audit_repo`, y
   `sqlite_agent_state.py:161-162` sale por la puerta de atrás. El `reason` de `8907b94` se
   calcula y se descarta. **Es el único hallazgo con consecuencia de seguridad.**
2. **PROV-03 — la tarjeta de Anthropic sigue sin poder conectar.** El bug del UUID está
   arreglado (gemini devuelve el error real del proveedor), pero la sonda de anthropic sale a
   `api.anthropic.com/chat/completions` → 404 con cualquier clave, y la UI sólo activa con
   `ok === true`. Síntoma idéntico al de la matriz anterior, causa distinta.
3. **CLI-N2 — `--porcelain` sólo lo honran los verbos T004.** `safent --porcelain status`
   escribe texto humano en stdout (`safent:788-796`) y `cmd_url` escribiría ahí el vale `?k=`.
   Sin impacto hoy (ninguno de los dos está en la tabla de verbos de `app-engine.md §4`).
4. **UPD-N2 / A-07 (menores) —** `GET /api/v1/system/update` no lleva los campos `available` /
   `current` (VersionSet) / `to` / `checked_at` que `update.md §3` promete; y `/openapi.json` se
   sirve **sin bearer** mientras todas las rutas que describe dan 401.

### Frente a la matriz anterior (114 PASS / 11 FALLA)

- **Cerradas 8 de las 11:** MCP-04 (EXDEV, vía REFER sólo en RUNTIME), MCP-05 (`BRAVE_API_KEY`),
  SEG-15 (ida y vuelta del interruptor sin dead-end), PROV-02 (`model.default` en `config.yaml`),
  PROV-05 (el proveedor nuevo gobierna el turno inmediato), MEM-06 (404), BKP-01 (`restore`
  crea el contenedor de verdad) y **R17** (`safent brake release` es la salida soberana real).
- **Cerradas en código, no ejecutables aquí:** CLI-08 (`_companion_env` en `safent:1041`) y
  CLI-10 (`_persisted_ads_image` en `safent:947`).
- **Sin verificar en la DGX:** ADS-02 (companion ajeno en este host; probada en el Mac).
- **A medias:** PROV-03.
- **Regresiones nuevas: ninguna.** Todo lo que la matriz anterior daba por PASS y se volvió a
  ejercitar (arranque, 0 unidades failed, Landlock, 401 en toda la API, WebSockets con token,
  freno → 423, persistencia por backup/restore, validación de MCP, egress) sigue verde. Los tres
  hallazgos nuevos (CLI-N4, CLI-N2, A-07) son de superficies que la pasada anterior no cubría.
