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
