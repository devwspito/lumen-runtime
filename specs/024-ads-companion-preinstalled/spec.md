# 024 — `safent-ads` preinstalado (companion), sin escribir URL

**Rama**: `feat/safent-next` · **Autor**: software-architect (sombrero de seguridad)

## 1. Problema

Hoy `safent-ads` es un MANAGED_REMOTE que el dueño enchufa a mano: *Herramientas → pegar
`https://…/mcp` → Conectar* (`run-safent.sh`, cabecera; `mcp_api.py`
`POST /managed-remote/{slug}/connect`). El dueño del producto exige que aparezca **ya
instalado**, igual que `excel`/`word`/`powerpoint` de `seed/mcp-servers.json`, y que
**nadie escriba una URL**. El servicio de ads ya existe y es multi-contenedor
(`ads-api` + `ads-worker` + `ads-broker` + `ads-db` Postgres, con separación de uid y de
secretos). La jaula, en cambio, es default-deny: el netns `hermes-mcp` sólo sale al proxy
(`mcp-ns.nft`), el forward host tira `10/8, 172.16/12, 192.168/16, 127/8, 169.254/16,
100.64/10` (`mcp-host.nft`) y el proxy rechaza cualquier host que resuelva a privada
(`proxy_handler.py::_resolve_external_ips`). Un contenedor hermano es, hoy, inalcanzable
por partida doble — a propósito.

## 2. Opciones consideradas

### (A) Companion arrancado por el lanzador, con allow-list declarada en la jaula — **ELEGIDA**

`run-safent.sh` levanta el compose de ads en una red fija (`safent-companions`,
`10.201.0.0/24`, `ads-api` en `10.201.0.10:8443` con TLS), genera CA + bearer al instalar y
arranca Safent unido a esa red con tres montajes **de sólo lectura** bajo `/etc/hermes/`. El
runtime lee el destino de `/etc/hermes/companions.json` y, **en el arranque**, genera **una**
regla nft con IP y puerto fijados.

- Instalación de un comando: sí (`safent update` lo re-provisiona).
- Sin escribir URL: sí — la URL es `https://ads.safent.internal:8443/mcp`, constante.
- Invariantes de la jaula: el proxy **no se toca**; su anti-SSRF sigue absoluto porque el
  tráfico al companion **no pasa por el proxy**. El anti-pivot sobrevive: se abre un único
  `daddr:dport`, el resto de `10/8` sigue cayendo.
- Confused deputy vía config-sync: imposible por construcción — no hay verbo D-Bus que
  escriba `companions.json`, el fichero es un bind read-only de host y `/etc` ya es de sólo
  lectura para el daemon (`ProtectSystem=strict`).
- Conserva el servicio Postgres ya construido: intacto, sin reescribir nada.
- Mac: idéntico (todo vive dentro de la VM de `podman machine`; los binds cuelgan de `$HOME`,
  que la máquina comparte por defecto).
- Enterprise: el bundle sigue autorizando *uso*, nunca *destino*.
- Actualización: `safent companion update` desacopla la versión de ads de la imagen de Safent.

### (B) Ads horneado dentro de la imagen de Safent como MCP stdio sembrado — **descartada**

Obliga a tirar Postgres por SQLite, a meter planificador y bot de Telegram en proceso, y a
que `shell_server` sirva el panel. Peor: **destruye el aislamiento de credenciales** que el
servicio ya tiene (`ads-broker` uid 10002, `secrets/broker.env` exclusivo, `read_only`):
dentro de la jaula todo pasaría a ser `hermes-sandbox` en un único netns.
Y las credenciales de Google/Meta exigirían abrir `googleads.googleapis.com` y
`graph.facebook.com` en el proxy **para todo el plano MCP** — un ensanchamiento mucho mayor
que el destino único de (A). Además acopla cada release de ads a un rebuild de la imagen.

### (C) Managed-remote sobre HTTPS pública con URL auto-provisionada — **descartada**

Auto-provisionar la URL exige que *algo* la fije sin el dueño. Si la fija la nube, se
reabre el confused deputy que `_grant_mcp_egress_for_managed_remote` cierra hoy ("nunca del
argv del bundle"): quien elige el destino de egreso pasa a ser el plano de control. Exige
además DNS y TLS público por tenant (coste recurrente) y deja sin producto la instalación
Community/offline. Se conserva como **variante Enterprise alojada**, no como defecto.

### No exploradas (y por qué)

Transporte MCP por socket Unix compartido (requiere un `Transport` nuevo *y* dar al
contenedor de ads escritura sobre el volumen de estado de Safent — peor postura);
WireGuard/Tailscale entre contenedores (dependencia de red ajena; fuera de alcance por
mandato); ads como unidad systemd dentro del contenedor de Safent en su propio netns
(es (B) con más pasos y sigue sin Postgres).

## 3. Invariantes (no negociables)

- **INV-1** El proxy de egreso no cambia. `_resolve_external_ips` sigue rechazando toda
  privada, sin excepciones ni listas.
- **INV-2** El conjunto de destinos alcanzables desde `hermes-mcp` se fija **en el arranque**
  desde un fichero root, read-only. Ninguna ruta de ejecución (D-Bus, config-sync, modelo,
  socket de control del proxy) puede añadir uno.
- **INV-3** Un companion abre exactamente un `ip daddr` + un `tcp dport`. Ni la subred, ni la
  base de datos del companion, ni el gateway.
- **INV-4** El bearer nunca aparece en `argv`, ni en la entrada persistida de Neus, ni en
  ninguna respuesta REST, ni en el log ni en la cadena de auditoría. Sólo viaja un `bearer_ref`.
- **INV-5** `safent-ads` es **sembrado** en el sentido de ciclo de vida (sin URL, sin prompt de
  escaneo de instalación) pero sigue siendo `MANAGED_REMOTE` en el sentido de confianza: las
  escrituras no son auto-ejecutables. Los dos ejes se desdoblan; `_BUILTIN_MCP_SLUGS` **no** lo
  recibe.
- **INV-6** El plano de control en la nube autoriza *el uso* del slug; **nunca** su URL, su IP,
  su CA ni su bearer.
- **INV-7** TLS obligatorio con CA privada fijada. Sin listener en claro en el companion.

## 4. Requisitos funcionales

- **FR-1** Tras `curl … | sh`, `safent-ads` aparece en Herramientas ya registrado, sin campo de URL.
- **FR-2** El estado del slug se muestra con precisión: `aprovisionando`, `esperando servicio`,
  `instalado · esperando cuentas`, `listo`, `versión incompatible`.
- **FR-3** Si el companion no arranca, Safent arranca igual; las tools de ads **no aparecen** en
  el catálogo (ausentes, no presentes-y-rotas).
- **FR-4** `safent companion status|update|rotate|remove` existe y es idempotente.
- **FR-5** Instalaciones existentes con URL remota guardada siguen funcionando; el companion,
  si está, tiene precedencia y lo dice en la UI sin borrar la entrada anterior.
- **FR-6** El dueño puede rechazar el companion (`run-safent.sh --no-companion`) y seguir con (C).
- **FR-7** Las tools de escritura de ads siguen exigiendo HITL; la tabla de prefijos
  auto-ejecutables revisada (`_MANAGED_REMOTE_AUTO_PREFIXES["safent-ads"]`) no se amplía.

## 5. Contrato Enterprise

El bundle firmado autoriza con los verbos **ya permitidos** en `config_sync/applier.py`:
`set_agent_access_scope` con `authorized_mcp_servers: ["safent-ads"]` y un `policy_overlay`
que use el eje `approval` (`resolve_tool_approval_override`) para forzar `hitl` en las tools
de gasto. No se añade ningún verbo nuevo a la allow-list del applier: **si la nube no tiene
verbo para fijar un destino, no puede fijarlo**. Cuestión abierta para el dueño (§ plan.md):
hoy un overlay `approval: "auto"` puede *ensanchar*; se recomienda rechazarlo en el límite
D-Bus para slugs MANAGED_REMOTE, porque una nube que auto-aprueba gasto es un confused
deputy sobre dinero.

## 6. Criterios de éxito

- **SC-1** Instalación limpia en Linux y en Mac: cero URLs escritas, `safent-ads` operativo.
- **SC-2** Desde el netns `hermes-mcp`, `10.201.0.11:5432` (ads-db) y `10.201.0.1` (gateway)
  son inalcanzables; `10.201.0.10:8443` es alcanzable. Verificado en smoke de contenedor.
- **SC-3** Un `companions.json` con `http://`, con puerto distinto de 8443, con IP fuera de
  `10.201.0.0/24`, con huella que no cuadra con la CA en disco, o con permisos de grupo/otros,
  se ignora entero (fail-soft a "sin companion").
- **SC-4** Un bundle Enterprise que intente fijar URL/IP/bearer es rechazado o inerte.
- **SC-5** La suite `pytest tests/unit/agents_os/ tests/unit/cli/ tests/unit/apps/ -q` sigue verde.

## 7. Fuera de alcance · supuestos

Fuera: negocio de ads, Telegram, webhooks de plataforma, motor de creatividad.
Supuestos: `10.201.0.0/24` y `8443` son constantes del producto (si chocan, el
aprovisionamiento **falla ruidoso**, no busca otra); `ads.safent.internal` se fija por
`/etc/hosts` y nunca se resuelve por DNS; la imagen del companion la firmamos nosotros.
