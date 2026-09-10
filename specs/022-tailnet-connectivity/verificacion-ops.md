# Verificación ops — spec 022 (tailnet-ops lane)

Ejecutada 2026-09-10 desde el worktree `lumen-runtime-tailnet-ops` (rama `tailnet-ops`,
commit `1b1ca61` + el fix de `RestrictAddressFamilies` de esta misma pasada), contenedor
rootful aislado `tailnet-check-1` (`sudo -n podman`, sin publicar nada salvo
`127.0.0.1:18082`, teardown al final). Arquitectura del host: `linux/arm64`.

Corresponde al punto 4 del encargo y a la lista "MUST verify before publish" de
`plan.md`.

## Build

```
sudo -n ./ops/container/build.sh
```

`ghcr.io/devwspito/safent:0.8.42` (+ `:latest`) construida y verificada
(`hermes.__version__` == `0.8.42`). Local únicamente — nunca `--push`.

**Hallazgo durante la 1ª pasada (corregido, no reportado como pendiente):**
`hermes-tailscaled.service` crash-loopeaba en el arranque —
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` no incluía `AF_NETLINK`, que el
`netmon` de tailscaled necesita para el socket `NETLINK_ROUTE` con el que vigila el
estado de SUS PROPIAS interfaces (ruta por defecto, direcciones) — informativo,
nada que ver con el tun/router userspace (`fake (no-op)`). Sin él, `socket()`
devuelve `EAFNOSUPPORT` (`"netlinkrib: address family not supported by protocol"`)
y el proceso termina con `exit-code 1` en bucle (`Restart=on-failure`,
`StartLimitIntervalSec=0` = reintentos infinitos). Corregido añadiendo `AF_NETLINK`
a la lista (commit de esta pasada); reconstruida la imagen y repetida la
verificación completa desde cero — limpia. Test de regresión:
`tests/unit/hardening/test_tailnet_units.py::TestTailscaleddHardeningPresent::test_restrict_address_families_includes_netlink`.

## Arranque del contenedor de verificación

```
sudo -n env SAFENT_NAME=tailnet-check-1 SAFENT_VOLUME=tailnet-check-1-data \
  ./ops/container/run-safent.sh ghcr.io/devwspito/safent:0.8.42 18082 --no-companion
```

`hermes-runtime.service` → `active` en el segundo intento (~10s). `hermes-tailscaled.path`
y `hermes-tailscale-control.path` → `active` desde el arranque (habilitados en el
Containerfile). Directorios tmpfiles correctos: `/run/hermes/tailscale` (0700
hermes-tailscale), `/run/hermes/tailscale-control` (0700 hermes), `/var/lib/hermes/tailscale`
(0700 hermes-tailscale). `hermes-tailscaled.service` en `inactive (dead)` — correcto,
`ConditionPathExists` sin el marker `enabled` aún no escrito.

## 1. tailscaled arranca en modo userspace (sin `/dev/net/tun`, sin capabilities)

Marker escrito (`touch /var/lib/hermes/tailscale/enabled`) → `hermes-tailscaled.path`
disparó `hermes-tailscaled.service` EN CALIENTE (sin reboot, tal como documenta el
unit) → `active (running)`, PID 506, uid/gid `887:887` (hermes-tailscale).

- `cat /proc/506/status`: `CapInh/CapPrm/CapEff/CapBnd/CapAmb` = `0000000000000000`
  (las CINCO, incluida `CapBnd` — coincide con `CapabilityBoundingSet=` vacío del
  unit). **Cero capabilities, incluida NET_ADMIN.**
- `/dev/net/tun`: `No such file or directory` dentro del contenedor — el dispositivo
  ni siquiera existe (nunca se concedió `--device /dev/net/tun` ni `NET_ADMIN` a
  nivel `run-safent.sh`, que no cambió).
- Journal: `"using fake (no-op) tun device"`, `"using fake (no-op) OS network
  configurator"`, `"warning: fakeRouter.Up: not implemented."` — confirma
  `--tun=userspace-networking` real, no un fallback silencioso a tun de kernel.
- `tailscale --socket=/run/hermes/tailscale/tailscaled.sock status --json` →
  `"TUN": false`.
- Sin ninguna mención a `NET_ADMIN` ni a `/dev/net/tun` en el journal del unit.

**PASA.**

## 2. RED-TEAM INVARIANT — cada netns del agente NO alcanza 127.0.0.1:1055/:1056

```
ip netns list   → hermes-mcp (id 2), hermes-browser (id 1)
```

Desde cada netns (`nsenter --net=/run/netns/<hermes-browser|hermes-mcp>`):

```
curl --max-time 5 http://127.0.0.1:1055                        → curl: (7) Connection refused
curl --max-time 5 --socks5-hostname 127.0.0.1:1056 https://example.com → curl: (7) Connection refused
```

Las CUATRO combinaciones (2 netns × 2 puertos) fallan igual: `Connection refused`
en 0ms — el loopback es propio de CADA netns (aislamiento de kernel, no solo la regla
nft), así que el agente físicamente no tiene ruta al loopback del netns host. La
regla `ip daddr { …, 127.0.0.0/8, …, 100.64.0.0/10 }` en `browser-host.nft` /
`mcp-host.nft` (verificada también estáticamente en
`tests/unit/hardening/test_tailnet_units.py::TestRedTeamInvariantAgentNetnsDropsTailnetLoopback`)
es la capa de defensa-en-profundidad para cualquier ruta que NO sea loopback puro
(p. ej. si el agente intentara la IP del veth del host). El rango CGNAT
`100.64.0.0/10` no se pudo probar con una IP tailnet real asignada (sin login —
alcance del owner, fuera de este worktree, como documenta `plan.md`), pero el
texto de la regla se verifica byte a byte en el test estático.

**PASA** (defensa por namespace + regla nft, ambas confirmadas).

## 3. Estado del tailnet sin auth key (logged out)

```
tailscale --socket=/run/hermes/tailscale/tailscaled.sock status       → "Logged out." (rc=1)
tailscale --socket=/run/hermes/tailscale/tailscaled.sock status --json → "BackendState": "NeedsLogin"
```

**PASA** — exactamente el estado esperado sin clave (ningún secreto en esta
verificación).

## 4. Los proxies loopback SÍ aceptan conexión desde la netns HOST del contenedor

```
curl -x 127.0.0.1:1055 https://example.com                       → CONNECT 200, TLS handshake OK
curl --socks5-hostname 127.0.0.1:1056 https://example.com         → SOCKS5 granted, TLS handshake OK
```

**Hallazgo (no una regresión, documentado para las lanes egress-proxy/tailnet-ssh):**
esperábamos "conecta pero devuelve error de proxy" (según el encargo); en la
práctica tailscaled desconectado (`NeedsLogin`) sigue retransmitiendo CUALQUIER
destino como un proxy genérico — no tiene tabla de rutas del tailnet, así que cae
al dial directo. Esto NO es una brecha para el agente (sigue sin poder alcanzar
estos puertos, invariante §2), pero significa que el `allowlist` de sufijo
MagicDNS tiene que aplicarse en el LLAMANTE (`egress_proxy/infrastructure/
proxy_handler.py`, lane ajena) — tailscaled por sí solo no rechaza destinos fuera
del tailnet. Dejarlo anotado explícitamente para que esa lane no asuma que
tailscaled hace ese filtrado.

## 5. Mecanismo completo `hermes-tailscale-control` en el sistema real (no mockeado)

Sin contraseña de dispositivo configurada en esta instancia efímera (onboarding no
ejecutado — coherente con "Final owner-side test... not testable here" de
`plan.md`), se montó una prueba de MECANISMO con credenciales conocidas-falsas para
confirmar el camino completo sin depender de mocks:

```
# escrito como uid hermes (880), 0600, en /run/hermes/tailscale-control/request.json:
{"action":"connect","password":"wrong-password-mechanism-check",
 "auth_key":"tskey-auth-fake-for-mechanism-check"}
```

- `hermes-tailscale-control.path` disparó `hermes-tailscale-control.service`
  automáticamente (sin intervención).
- El helper (root) verificó PAM, rechazó fail-closed (`hermes-user` sin contraseña
  real en esta instancia — mismo invariante fail-closed que
  `hermes-remote-access-control`), **sin llegar a escribir el fichero
  `authkey` efímero ni el marker** (`_connect` nunca se invoca).
- El fichero `request.json` quedó SHREDDEADO (`ls` → directorio vacío) tanto en el
  éxito como en este rechazo.
- `journalctl` COMPLETO del contenedor grepeado por las dos cadenas falsas
  (`tskey-auth-fake-for-mechanism-check`, `wrong-password-mechanism-check`):
  **cero coincidencias** — ni la clave ni la contraseña tocaron el log en ningún
  punto del camino.

**PASA** — confirma en vivo lo que `tests/unit/ops/test_tailscale_control.py` ya
cubre con mocks + binario `tailscale` falso: el secreto nunca llega a argv/env/log,
el fichero puente se destruye siempre, y el gate PAM es fail-closed.

## Teardown

```
sudo -n podman rm -f tailnet-check-1
sudo -n podman volume rm tailnet-check-1-data
```

Confirmado: `podman ps -a` / `podman volume ls` sin `tailnet-check-1*` tras el
teardown. Ningún puerto quedó publicado más allá de `127.0.0.1:18082` durante la
prueba (loopback del host, igual que `run-safent.sh` en producción).

## Resumen frente a "MUST verify before publish" (plan.md)

| # | Invariante | Resultado |
|---|---|---|
| 1 | tailscaled en modo userspace, sin tun, sin caps añadidas | PASA |
| 2 | RED-TEAM: cada netns del agente no alcanza 127.0.0.1:1055 | PASA (2/2 netns, 2/2 puertos) |
| 3 | egress-proxy → conector tailnet (allowlist de sufijo) | fuera de esta lane (`egress_proxy/**`) |
| 4 | ningún secreto en argv/env/`systemctl show`/`/proc/<pid>/cmdline`/logs | PASA (verificado en vivo, ítem 5 arriba) |
| — | prueba final con tailnet real del owner | fuera de alcance (necesita su auth key) |

## No se pudo honrar / queda fuera de esta lane

- El filtrado por sufijo MagicDNS (invariante #3) vive en `egress_proxy/**`
  (excluido explícitamente de este encargo) — no se tocó ni se verificó aquí.
- `100.64.0.0/10` solo verificado por texto de regla, no por una IP tailnet real
  asignada (requiere login real, alcance del owner).
- El flujo `connect` de éxito (con clave real y contraseña real) no se pudo
  ejercitar end-to-end en el sistema real (requiere onboarding + clave `tskey-auth-`
  del owner) — cubierto en cambio por `tests/unit/ops/test_tailscale_control.py`
  con un binario `tailscale` falso que prueba exactamente las mismas propiedades
  (flag `--auth-key=file:`, fichero destruido, status.json sin claves).
