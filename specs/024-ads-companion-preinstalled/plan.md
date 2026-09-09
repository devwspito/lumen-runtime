# 024 — Plan: companion `safent-ads` preinstalado

Implementa `spec.md` §2 opción (A). Identificadores en inglés, prosa en español.

## 1. Mecanismo

### 1.1 Aprovisionado (host, una vez, idempotente)

`ops/container/companions/ads/provision.sh` corre **antes** de arrancar Safent, invocado por
`run-safent.sh` y por `safent update`. Estado en `~/.safent/companions/ads/` (bajo `$HOME`
porque es lo que `podman machine` comparte con la VM en Mac):

1. `podman network create safent-companions --subnet 10.201.0.0/24` (idempotente; si la
   subred está ocupada, **falla ruidoso** y Safent arranca sin companion).
2. Si no existe `tls/ca.crt`: CA ECDSA P-256 (10 años) + hoja para `SAN DNS:ads.safent.internal`
   (825 días). Claves `0600` del usuario. `ca.fingerprint` = SHA-256 del DER.
3. Si no existe `bearer`: `openssl rand -hex 32`, `0400`.
4. Escribe `companions.json` (0444, `chown root:root` si hay privilegios).
5. Descarga `SAFENT_ADS_IMAGE` (por defecto `ghcr.io/devwspito/safent-ads:latest` — el
   artefacto de release del dueño; override a `safent-ads:local` para desarrollo) **solo si
   no está ya en local**. Nunca la construye aquí (nada de `podman build`, no hay publicación
   desde una máquina de desarrollador).
6. La primera vez (marcador: `secrets/api.env` ausente), genera y escribe **una sola vez**,
   `0600`, sin volver a tocarlos nunca: `secrets/api.env` (`ADS_MCP_TOKEN` = el bearer del
   paso 3, `ADS_SESSION_SECRET`, `ADS_TOTP_ENC_KEY`, `ADS_APPROVAL_SIGNING_KEY`) y
   `secrets/broker.env` (`ADS_APPROVAL_PUBLIC_KEY`, `ADS_CREDENTIAL_MASTER_KEY`,
   `ADS_BROKER_ALLOWED_UIDS=10001`, `ADS_BROKER_HARD_CAPS_FILE`, `ADS_CREDENTIAL_STORE_DIR`,
   `ADS_BROKER_SOCKET`). El par de aprobación sale de `python -m safent_ads.tools.gen_keys`
   ejecutado dentro de la propia imagen (`--network none`). Si existe `$STATE/vendor.env`
   (credenciales de la MCC de Google / app de Meta del propio Safent, puestas a mano por el
   dueño), sus líneas `GOOGLE_ADS_*`/`META_*` se incorporan a `broker.env` en **cada**
   ejecución sin duplicar claves — así el dueño puede añadirlas después del primer arranque.
7. La primera vez (marcador: `caps.yaml` ausente), copia
   `ops/container/companions/ads/caps.template.yaml` a `$STATE/caps.yaml` (0644): topes por
   defecto de `spec.md §15 D-A1` y `accounts: {}` — fail-closed, ninguna cuenta autorizada
   hasta que el dueño añada su `platform_account_id` real a mano.
8. `podman compose -p safent-ads -f ops/container/companions/ads/compose.yaml up -d`.
   Ese compose es **nuestro**, no el de desarrollo del repo de ads: fija la imagen del paso 5,
   `ipv4_address: 10.201.0.10`, monta la hoja TLS y `caps.yaml` de solo lectura, y **no
   publica** ningún puerto salvo el panel en `127.0.0.1:8443` para el humano.
9. Espera a `GET https://ads.safent.internal:8443/mcp/health` (con `--cacert` la CA propia y
   `--resolve` fijado a la IP, sin depender de que este HOST resuelva ese nombre) hasta 60 s;
   un `401` sin bearer ya prueba que el companion está vivo (el endpoint exige bearer). Fail-
   soft: si no responde, Safent arranca igual (FR-3).

### 1.2 Arranque de Safent

`run-safent.sh` añade `--network safent-companions` y tres binds **read-only**:

```
-v $STATE/companions.json:/etc/hermes/companions.json:ro
-v $STATE/tls/ca.crt:/etc/hermes/companions/ads-ca.crt:ro
-v $STATE/bearer:/etc/hermes/companions/ads.bearer:ro
```

`/etc/hermes/` es la elección deliberada: **no** es `/var/lib/hermes` (volumen de estado que
el daemon y config-sync escriben), es un bind read-only del host, y las units endurecidas ya
llevan `ProtectSystem=strict` — `/etc` es de sólo lectura para el daemon. Ni el modelo, ni
config-sync, ni el propio daemon pueden crear o alterar un companion. Ésa es la denegación
"por construcción" que pide INV-2.

`companions.json`:

```json
{"version": 1, "companions": [{
  "slug": "safent-ads",
  "url": "https://ads.safent.internal:8443/mcp",
  "ip": "10.201.0.10", "port": 8443,
  "ca_path": "/etc/hermes/companions/ads-ca.crt",
  "ca_fingerprint": "sha256:AB…",
  "bearer_ref": "file:/etc/hermes/companions/ads.bearer"}]}
```

Nunca el valor del bearer: sólo una referencia, para que `GET /api/v1/mcp` pueda mostrarlo sin
filtrar nada (INV-4).

### 1.3 Cargador y validación

`src/hermes/shell_server/companions.py`, espejo estricto de `managed_remote_endpoints.py`:
ruta constante (sin override por env — un override es una perilla alcanzable), rechaza el
fichero si no es de uid 0 o si tiene escritura de grupo/otros, y valida `slug` contra
`_COMPANION_SLUGS = frozenset({"safent-ads"})`, esquema `https`, host terminado en
`.safent.internal` (no literal IP, **no** nombre público — exactamente el inverso de la regla
managed-remote), `ip` dentro de `10.201.0.0/24`, `port ∈ {8443}`, y `ca_fingerprint`
**recalculada del DER en `ca_path`**: la huella del JSON no es la raíz de confianza, es una
comprobación cruzada — cambiar sólo uno de los dos falla. Fail-soft a `{}`.

### 1.4 Semilla y confianza — el desdoble

Cuarta entrada en `ops/agents-os-edition/seed/mcp-servers.json`:

```json
{"server_id": "safent-ads",
 "label": "Safent Ads · campañas Google y Meta (preinstalado)",
 "argv": ["npx","-y","mcp-remote","https://ads.safent.internal:8443/mcp",
          "--header","Authorization: Bearer ${ADS_BEARER}"],
 "env": {"ADS_BEARER": "", "NODE_EXTRA_CA_CERTS": ""},
 "requires_companion": true}
```

El bearer **no** está en el argv: `mcp-remote` expande `${ADS_BEARER}` desde su propio entorno,
así que `ps` sólo ve el marcador. `_mcp_connect` rellena las dos claves declaradas-vacías desde
el companion — el mismo patrón que ya usa para `OPENAI_API_KEY` (rellena, nunca añade claves).
`NODE_EXTRA_CA_CERTS` fija la CA para el TLS de node; con la verificación de SAN y el pin de
`/etc/hosts` a la única IP permitida, eso es el pinning completo.

La decisión de diseño central: **ciclo de vida y confianza son ejes distintos y hoy están
fusionados en un `frozenset`**. Se introduce `_SEEDED_MCP_SLUGS` (viene de la imagen, sin URL,
sin prompt de escaneo, no resucita si el dueño lo borra) separado de la clasificación de
confianza. `safent-ads` entra en `_SEEDED_MCP_SLUGS` y **no** en `_BUILTIN_MCP_SLUGS`: sigue
siendo `MANAGED_REMOTE` (ya está en `_MANAGED_REMOTE_MCP_SLUGS`), así que las escrituras siguen
sin ser auto-ejecutables y `_MANAGED_REMOTE_AUTO_PREFIXES["safent-ads"]` — la lista revisada,
con `apply_defensive_action` escrito entero — no se toca.

### 1.5 De dónde sale el permiso de red

`_grant_mcp_egress_for_managed_remote` gana una **fuente 0** (`_resolve_companion_endpoint`)
con precedencia sobre `managed_remote_endpoints` y sobre el `cloud_endpoint` emparejado. Pero
un companion **no** empuja dominio al proxy: el tráfico no pasa por el proxy. La fuente 0 sólo
decide si registrar el servidor y qué estado mostrar.

El permiso real se genera **en el arranque**, no en el connect. Nueva oneshot
`hermes-companion-egress.service` (root, `Before=hermes-browser-netns.service`) ejecuta
`ops/agents-os-edition/scripts/hermes-companion-nft`, que lee y valida `companions.json` con el
mismo cargador y escribe en `/run/hermes/nft/` (tmpfs, se regenera cada arranque):

```
# companion-ads-fwd.nft   → incluido por mcp-host.nft ANTES del drop RFC1918
ip saddr 10.200.1.2 ip daddr 10.201.0.10 tcp dport 8443 accept comment "companion safent-ads"
# companion-ads-out.nft   → incluido por mcp-ns.nft en la chain output
ip daddr 10.201.0.10 tcp dport 8443 accept
# companion-ads-nat.nft   → masquerade sólo para ese destino
ip saddr 10.200.1.2 ip daddr 10.201.0.10 masquerade
```

`mcp-host.nft` y `mcp-ns.nft` sólo ganan `include "/run/hermes/nft/companion-*.nft"` en el punto
correcto (un glob sin coincidencias no es error). Un `drop` en nftables es terminal, así que la
regla **tiene que** ir en la misma chain y antes del drop de `10/8` — por eso se hace por
`include` y no por una tabla aparte. `host-input.nft` no se toca (el companion no es el host).
Se añade también `sysctl net.ipv4.conf.<iface-companion>.forwarding=1` y la línea
`10.201.0.10 ads.safent.internal` en `/etc/hosts`.

Propiedad anti-pivot conservada: se abre un `daddr`+`dport`. `ads-db` (`10.201.0.11:5432`),
`ads-broker`, el gateway `10.201.0.1` y todo el resto de `10/8`, `172.16/12`, `192.168/16`,
`127/8`, `169.254/16` siguen cayendo. Y el proxy, con su anti-SSRF intacto, sigue siendo el
único camino a Internet para cualquier otro MCP.

### 1.6 Mac y Linux

Idénticos: el companion vive en el mismo runtime de contenedores que Safent, dentro de la VM de
`podman machine` en Mac o en el host en Linux, así que `10.201.0.10` es alcanzable en ambos sin
port-forward. Diferencias reales: (a) en Mac los binds deben colgar de `$HOME` (montaje que la
máquina comparte por defecto; si el usuario cambió los mounts, `provision.sh` falla ruidoso);
(b) `nf_log_syslog` no existe en la VM de podman — ya contemplado, el drop aplica igual y sólo
se pierde el log; (c) Docker Desktop usa `docker network create --subnet`, mismo resultado.

## 2. STRIDE del delta

| # | Amenaza | Control | Dueño | Test |
|---|---|---|---|---|
| S-1 | Companion suplantado (otro contenedor ocupa `.10`, respuesta DNS falsa) | CA privada por `NODE_EXTRA_CA_CERTS` + SAN + pin en `/etc/hosts` + huella recalculada del DER | security-engineer | `test_companions_rejects_fingerprint_mismatch` |
| S-2 | Bearer robado | `0400` root, bind read-only, nunca en argv/entrada Neus/REST/log; `safent companion rotate` re-emite en ambos lados | backend-engineer | `test_companion_bearer_never_leaves_daemon` |
| T-1 | config-sync intenta añadir companion | No existe verbo; `/etc` read-only; la allow-list del applier no gana entradas | security-engineer | `test_bundle_cannot_set_companion` |
| T-2 | El modelo reescribe el endpoint | Mismo bind read-only + rechazo por propietario/permisos en el cargador | security-engineer | `test_companions_rejects_world_writable_file` |
| E-1 | Pivote a otros servicios locales por la regla | Regla `daddr`+`dport` única, generada en boot desde fichero read-only | devops-engineer | `test_companion_nft_rule_is_single_destination` + smoke `smoke_mcp_cannot_reach_ads_db` |
| T-3 | Degradación a texto plano | Cargador https-only + `port ∈ {8443}`; la regla sólo abre 8443; el companion no levanta listener en claro | backend-engineer | `test_companions_rejects_http_scheme` |
| I-1 | Secreto en logs/auditoría | Se registran slug e `ip:port`, nunca el bearer | backend-engineer | `test_companion_logs_have_no_secret` |
| D-1 | Companion caído | Connect fail-soft; las tools quedan **ausentes** del catálogo, no presentes-y-rotas; badge "esperando servicio" | backend-engineer | `test_companion_down_hides_tools_not_errors` |
| D-2 | Desfase de versiones Safent↔companion | `/mcp/health` devuelve `contract_version`; fuera del rango soportado ⇒ no se registra, badge "versión incompatible" y `safent companion update` | tech-lead | `test_companion_contract_version_gate` |
| E-2 | Ensanchamiento del auto-ejecutable | Tabla de prefijos revisada, `apply_defensive_action` literal | (ya cubierto) | `TestSafentAdsNeverAutoExecutesSpendVerbs` |

## 3. Enterprise

El bundle firmado usa **verbos ya permitidos**: `set_agent_access_scope` con
`authorized_mcp_servers: ["safent-ads"]` y `policy_overlay` con el eje `approval`
(`resolve_tool_approval_override`) para forzar `hitl` en las tools de gasto. La nube **no puede**
fijar URL, IP, CA ni bearer porque no hay verbo que lo aplique y `companions.json` está fuera de
su alcance: autoriza el *uso*, la instalación local decide el *dónde*.

**Decisión abierta para el dueño**: hoy un overlay `approval: "auto"` puede *ensanchar* lo que el
clasificador marcó como no-auto. Se recomienda que `_validate_policy_overlay_shape` rechace
`"auto"` para slugs `MANAGED_REMOTE` — una nube que auto-aprueba gasto es un confused deputy
sobre dinero. Cambia el contrato externo del bundle, así que va al dueño, no se decide aquí.

## 4. Plan de implementación

**Runtime** (`~/Desktop/lumen-runtime-next`):

| Fichero | Cambio | LOC |
|---|---|---|
| `ops/agents-os-edition/seed/mcp-servers.json` | 4ª entrada | 8 |
| `src/hermes/shell_server/companions.py` | **nuevo** cargador + validación | 140 |
| `src/hermes/agents_os/infrastructure/dbus_runtime_service.py` | `_SEEDED_MCP_SLUGS` (desdoble); fuente 0 del grant; relleno de `ADS_BEARER`/`NODE_EXTRA_CA_CERTS` en `_mcp_connect`; salto del escaneo de instalación para slugs sembrados; estado de companion en `list_mcp_servers`; +2 claves en `_MCP_BYOK_ENV_KEYS` | 115 |
| `ops/agents-os-edition/scripts/hermes-mcp-launcher` | espejo en `_ALLOWED_ENV_KEYS` | 2 |
| `ops/agents-os-edition/netns/mcp-host.nft`, `mcp-ns.nft` | `include` del glob, antes del drop | 4 |
| `ops/agents-os-edition/scripts/hermes-companion-nft` | **nuevo** generador de reglas + `/etc/hosts` | 90 |
| `ops/agents-os-edition/systemd/hermes-companion-egress.service` | **nueva** oneshot | 25 |
| `ops/container/Containerfile` | COPY de `ops/container/companions/ads/` al lado del seccomp | 4 |
| `ops/container/companions/ads/compose.yaml` | compose fijado: entrypoints reales, TLS, `deploy.replicas: 1`, `caps.yaml` bind, `credential-store` | 183 |
| `ops/container/companions/ads/provision.sh` | red + CA + bearer + imagen + secretos (api.env/broker.env) + caps.yaml + up + wait `/mcp/health` | 259 |
| `ops/container/companions/ads/caps.template.yaml` | **nueva** plantilla de topes duros (fail-closed, `accounts: {}`) | 29 |
| `ops/container/run-safent.sh` | fase companion, `--network`, 3 binds, `--no-companion`, `SAFENT_ADS_IMAGE` de conveniencia en dev | 45 |
| `safent` (CLI) | fase companion antes de `_run` (fetch image→raw→caché igual que el seccomp), `--no-companion`, `uninstall` hace `compose down` + borra la red | 105 |
| `tests/unit/ops/test_companion_provision.py` | **nuevo** — extremo a extremo de `provision.sh` contra estado temporal, podman/curl fingidos | 283 |

Pendiente, NO implementado en esta pasada (fuera del alcance de esta corrección — el CLI sólo
gana la fase de aprovisionamiento antes de `_run`/`uninstall`, no subcomandos nuevos):
`safent companion status\|update\|rotate\|remove` como comandos de primer nivel. Hoy
`safent update` re-provisiona el companion como efecto lateral de recrear el contenedor (llama
a `_run`, que llama a `_provision_companion`), pero no hay un comando dedicado a
inspeccionar/rotar/eliminar sólo el companion sin tocar Safent.
| `src/hermes/shell_server/cowork/mcp_api.py` | estado en el listado; `PUT managed-remote-endpoints/safent-ads` → 409 si hay companion | 25 |
| `frontend/src/views/McpView.tsx` | badge de estado, oculta el campo URL (**frontend-engineer**) | 40 |

**Tests**: `tests/unit/shell_server/test_companions.py` (180) · `tests/unit/agents_os/test_companion_egress_source.py`
(90) · `tests/unit/agents_os/test_seeded_vs_builtin_trust.py` (60) · `tests/unit/hardening/test_companion_nft_generation.py`
(120, fichero de reglas golden) · `tests/unit/config_sync/test_bundle_cannot_set_companion.py` (50) ·
`tests/vm/test_companion_smoke.py` (120: arranca Safent + companion, comprueba tools listadas,
`10.201.0.11:5432` inalcanzable desde `hermes-mcp`, y que un `companions.json` con `http://` se ignora).

**Lo que debe exponer el servicio de ads** (~60 LOC allí, fuera de este worktree):
`GET /mcp/health` protegido por bearer devolviendo `{status, contract_version, accounts_linked:{google,meta}, db}`;
comparación del bearer en tiempo constante en `/mcp`; terminación TLS en 8443 con la hoja
provista en la instalación (`--ssl-certfile/--ssl-keyfile`) y **eliminación del listener en claro**;
el panel se sirve por ese mismo listener.

## 5. Migración de instalaciones existentes

Instalaciones con `managed_remote_endpoints["safent-ads"]` puesto a mano: al primer arranque con
companion presente, **gana el companion** (fuente 0), se registra una vez
`hermes.dbus.companion_supersedes_endpoint`, **no se borra** la entrada anterior (reversible) y la
UI dice "ahora local · el endpoint remoto guardado quedó inactivo". El marcador
`mcp-seeds-imported.json` impide re-importar la semilla si el `server_id` ya existía, así que
`provision.sh`, al terminar, dispara la re-registración por la ruta ya existente
`POST /managed-remote/{slug}/connect` cuando el argv persistido no coincide con el del companion
(~20 LOC). Quien de verdad opera un plano de ads alojado usa
`safent companion adopt --keep-remote` y se queda en la opción (C).

**Tamaño total**: ~780 LOC de runtime + ~220 de ops del companion + ~620 de tests + ~40 de frontend.
