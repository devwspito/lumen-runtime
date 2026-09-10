# Contract — `app-engine`: el envoltorio nativo ↔ el CLI embebido

**Fuente de verdad de la forma.** El envoltorio (`desktop/src-tauri`) es el único
consumidor; el CLI embebido (`safent`, con `ops/container/run-safent.sh`) es el
único proveedor. Ninguna otra pieza habla este protocolo.

## 1. Invocación

```
<bundle>/engine/safent <verb> [args] --porcelain
```

Entorno que el envoltorio **fija siempre**:

| Variable | Valor | Por qué |
|---|---|---|
| `SAFENT_PODMAN` | ruta absoluta al podman empaquetado | Nunca el del PATH del usuario |
| `SAFENT_NO_BROWSER` | `1` | La app enseña el producto en su ventana |
| `SAFENT_NO_SELF_UPDATE` | `1` | El CLI viaja dentro del paquete; lo actualiza la app |
| `SAFENT_IMAGE` | `ghcr.io/devwspito/safent@sha256:…` | Digest, jamás una etiqueta |
| `SAFENT_ADS_IMAGE` | `ghcr.io/devwspito/safent-ads@sha256:…` | Idem (raíz del fallo CLI-10) |
| `SAFENT_STATE_HOME` | `~/.safent` | Un solo árbol de estado |

`--porcelain` **no** altera el comportamiento: sólo cambia el canal de progreso.
Sin la bandera, el CLI escribe el mismo texto humano de hoy (el operador de
terminal no pierde nada).

## 2. Canales

- **stdout** — exclusivamente **NDJSON**: un objeto por línea, UTF-8, sin
  agrupar. Nada más se escribe aquí.
- **stderr** — texto humano libre, para el diagnóstico. El envoltorio **no lo
  interpreta**; lo guarda para `diagnostics`.
- **código de salida** — `0` éxito · `10..39` fallo de dominio con `code`
  reportado en un evento `failed` previo · `1` fallo no clasificado.

## 3. Eventos (NDJSON)

```ts
type StageId =
  | 'preflight' | 'runtime_staging' | 'machine' | 'pull_engine' | 'pull_companion'
  | 'container' | 'health' | 'companion_scaffold' | 'companion_up'
  | 'companion_reload' | 'backup' | 'restore' | 'cleanup'

type EngineEvent =
  | { t: 'stage';    id: StageId; label: string; total_bytes?: number }
  | { t: 'progress'; id: StageId; done: number; total?: number; unit: 'bytes' | 'layers' | 'steps' }
  | { t: 'done';     id: StageId; ms: number }
  | { t: 'failed';   id: StageId; code: FailureCode; detail: string; retryable: boolean }
  | { t: 'facts';    facts: HostFacts }
  | { t: 'ready';    endpoint_ref: 'stdout-secret' }   // ver §5
```

**Invariantes de forma**

1. Ningún evento contiene el vale de arranque, el puerto, una URL local ni una
   credencial. `detail` es texto libre **saneado**: el CLI enmascara cualquier
   coincidencia con el vale antes de emitir.
2. `progress` llega al menos cada **5 s** mientras la etapa está viva
   (NFR-001/NFR-002). Una etapa sin `progress` durante 5 s es «estancada» y el
   envoltorio lo declara como tal.
3. Cada `stage` cierra con exactamente un `done` **o** un `failed`.
4. `label` viene en español y en vocabulario del dueño; el envoltorio lo pinta tal cual.

`FailureCode` (cerrado, estable):
`unsupported_os` · `unsupported_arch` · `insufficient_disk` · `insufficient_memory`
· `runtime_hash_mismatch` · `machine_create_failed` · `machine_start_failed`
· `userns_blocked` · `helper_denied` · `registry_unreachable` · `digest_mismatch`
· `pull_interrupted` · `port_exhausted` · `container_start_failed`
· `daemon_unhealthy` · `companion_network_conflict` · `companion_migration_failed`
· `companion_unreachable` · `backup_failed` · `restore_failed` · `clock_skew`.

## 4. Verbos

| Verbo | Qué hace | Idempotente | Etapas que emite |
|---|---|---|---|
| `facts --json` | Observa el equipo y emite **un** `facts`. No modifica nada. | sí (puro) | — |
| `stage-runtime` | Despliega y **verifica por sha256** el podman empaquetado. | sí | `runtime_staging` |
| `ensure-machine` | macOS: adopta una máquina apta o crea la nuestra desde la imagen empaquetada, **sin red**. Linux: prepara el almacén rootless. | sí | `machine` |
| `ensure-images` | `pull` por digest con reintentos y reanudación por capa. | sí | `pull_engine`, `pull_companion` |
| `up` | Elige puerto libre, crea el contenedor con la jaula canónica y espera salud. | sí | `container`, `health`, `ready` |
| `companion install\|repair\|remove [--purge]` | Ciclo de vida del compañero, imagen **por digest**. | sí | `companion_*` |
| `update --to <VersionSet>` | Copia previa, sustitución, migración y reversión. | sí | `backup`, `pull_*`, `container`, `health`, `restore?` |
| `uninstall --scope this-install` | Retira **sólo** lo que esta app instaló. | sí | `cleanup` |
| `diagnostics --out <path>` | Empaqueta eventos + stderr + `facts`. **Sin secretos.** | sí | — |

**`--scope this-install`** es obligatorio y corrige el hallazgo UPD-06 de la matriz
025: el `uninstall` de hoy borra agentes y binarios de otras instalaciones.

## 5. Entrega del vale de arranque

`up` termina emitiendo `{ t: 'ready', endpoint_ref: 'stdout-secret' }` y, **acto
seguido**, escribe **una única línea** en un descriptor dedicado (`--secret-fd N`,
por defecto 3) con la forma `http://127.0.0.1:<puerto>/?k=<vale>`.

- Nunca en stdout, nunca en argv, nunca en el entorno, nunca en un fichero.
- El envoltorio la lee, navega y **descarta** la cadena. No la persiste ni la
  vuelve a pedir salvo en un nuevo arranque del motor.
- Si el descriptor se cierra sin línea, el envoltorio entra en `reconnecting`
  (FR-012) — nunca en un bucle de peticiones.

## 6. Cancelación

`SIGINT` al proceso hijo: el CLI aborta la etapa viva, emite
`{t:'failed', code:…, retryable:true}` y sale. **Ninguna etapa deja el equipo a
medias**: el estado siempre es reanudable (los digests ya bajados se conservan; una
máquina a medio crear se marca y el reconciliador la retoma o la descarta).
Después del `stage` declarado como punto de no retorno (`applying_engine` en una
actualización), la cancelación se rechaza y el envoltorio deshabilita el gesto —
lo declara antes, nunca después (NFR-003).

## 7. Superficie que el envoltorio expone al producto (IPC de Tauri)

La página remota (`http://127.0.0.1:*`) **no** recibe permisos de núcleo. Sólo
sobreviven, sobre el origen local, los dos ya existentes del portapapeles del host
(`allow-read-host-clipboard`, `allow-write-host-clipboard`) y se añade **uno**:

```ts
/** Estado que el producto pinta sin poder provocarlo. Sólo lectura. */
declare function safentAppStatus(): Promise<{
  app_version: string
  update: { available: boolean; to?: { app?: string; engine?: string; companion?: string } }
  engine_phase: 'engine_ready' | 'updating' | 'degraded' | 'reconnecting'
}>
```

**Prohibido** exponer a la página: instalar, actualizar, desinstalar, navegar,
abrir en el navegador, leer ficheros o ejecutar procesos. Esas acciones se
disparan por el camino de la marca (`install-request.md`), que tiene vocabulario
cerrado y las cumple el agente anfitrión.
