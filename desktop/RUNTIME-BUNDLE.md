# Runtime empaquetado — cómo se rellena y cómo se consume

Fuente de verdad: `desktop/runtime-manifest.lock` (committeado). `desktop/scripts/
stage-runtime.sh` es el único programa que lo lee y el único que escribe bajo
`desktop/src-tauri/resources/runtime/` (gitignorado — nunca se commitea el
runtime en sí, sólo sus pines).

## Entrada del script: TRIPLE de Rust, no un nombre corto

```
desktop/scripts/stage-runtime.sh <triple>
```

Acepta exactamente los mismos triples que ya usa la matriz de
`agents-autonomy/.github/workflows/safent-desktop.yml` — sin capa de
traducción en el pipeline:

| Triple | Qué stagea | Lleva máquina |
|---|---|---|
| `aarch64-apple-darwin` | podman + gvproxy + vfkit (del `.pkg` oficial, expandido con `pkgutil --expand-full`, **nunca instalado**) + krunkit + imagen de máquina | sí (932 MB) |
| `x86_64-unknown-linux-gnu` | podman-static v6.1.1 amd64 (subconjunto curado) | no |
| `aarch64-unknown-linux-gnu` | podman-static v6.1.1 arm64 (subconjunto curado) | no |

`x86_64-apple-darwin` es un triple **reconocido y rechazado a propósito**: v6.1.1
no publica instalador macOS Intel (sólo `podman-installer-macos-arm64.pkg` +
`podman-remote-release-darwin_arm64.zip`), y coincide con `contracts/update.md`
(`darwin-x86_64` ausente a propósito en `latest.json`). Cualquier otro valor se
rechaza con un mensaje que lista los tres triples válidos. `aarch64-apple-darwin`
sólo puede stagearse en un runner Darwin (usa `pkgutil`); en Linux falla rápido
con un mensaje claro en vez de hacer trabajo a medias.

El script es idempotente (si lo ya stageado casa sha256 con el lock, no vuelve a
tocar la red) y falla cerrado (sha256 o tamaño que no casa ⇒ borra y aborta;
nunca deja un binario a medio verificar).

## Dónde caen los ficheros (contrato para quien consuma esto — T009)

`tauri.conf.json`'s `bundle.resources` usa **un** patrón glob —
`"resources/runtime/*/**/*": "runtime"` — porque en cualquier build real sólo
existe UN triple bajo `resources/runtime/` (el que ese runner stageó) y porque
ni `MacConfig` ni `LinuxConfig` (verificado contra `tauri-utils` 2.9.3, el
`Cargo.lock` real de este crate) tienen un campo `resources` propio —
`bundle.macOS`/`bundle.linux` sólo traen `files`/`dmg`/`deb`/`appimage`/`rpm`,
nada que sirva para esto. Un patrón glob en `bundle.resources` **aplana** la
estructura (comportamiento verificado leyendo `tauri-utils::resources` y
probado con el `glob` crate real 0.3.3 contra el árbol stageado de verdad): en
el paquete final, **todo** queda directo bajo `$RESOURCES/runtime/<nombre>`,
sin `bin/`/`libexec/podman/`/`etc/containers/` — cada nombre de fichero es único
en todo el conjunto (macOS + Linux), así que aplanar no pisa nada. En tiempo de
ejecución: `app.path().resource_dir()?.join("runtime").join("podman")`, etc.

`bundle.linux.deb.post_install_script` es donde va el postinst que instala el
ayudante privilegiado (perfil AppArmor + `newuidmap`/`newgidmap` +
`subuid`/`subgid`) — **no** lo toca esta entrega; es de `T011`
(`bootstrap_service.rs`/`privilege_helper.rs`, otro lane). Ver
`research.md` → «Decisión: Linux sin VM» → «Verificación en vivo» para la
condición exacta que dispara ese ayudante (no es «siempre en Ubuntu 24.04+»,
es `HostFacts.userNsAllowed == false`, medido con una sonda barata).

## Tamaños medidos (10-sep-2026, valores reales, no estimados)

| Triple | Descarga verificada | Stageado (subconjunto curado) |
|---|---|---|
| `aarch64-unknown-linux-gnu` | 31 857 193 B (podman-static) | 71 122 384 B ≈ 67,8 MiB |
| `x86_64-unknown-linux-gnu` | 34 628 342 B (podman-static) | 75 352 816 B ≈ 71,9 MiB |
| `aarch64-apple-darwin` | 76 334 314 B (`.pkg`) + 6 434 546 B (krunkit) + 931 934 236 B (imagen de máquina, por digest) | envoltorio Tauri ~12 MB + podman/gvproxy/vfkit/krunkit ~80 MB + imagen 932 MB ≈ **1,02–1,10 GB** |

Límite de GitHub Releases: **2 GiB por fichero**. El DMG macOS queda a ~45 % de
margen. Linux no lleva máquina — ni de lejos cerca del límite.

## Decisión Linux: rootless por defecto (confirmado en esta DGX)

Verificado en vivo (contenedores `desk2-*`, destruidos): el cage completo
(systemd PID1, Landlock, netns+nftables del navegador/MCP, mismas flags de
`run-safent.sh`) pasa **rootless**, tanto con el podman del sistema (perfilado)
como con un podman **reubicado y sin perfil AppArmor** (el sustituto más fiel
posible del binario empaquetado). El ayudante privilegiado no se ejecuta
incondicionalmente — sólo cuando una sonda barata (`<podman empaquetado>
unshare true`) confirma que el kernel lo exige. Detalle completo, con los
comandos exactos, en `research.md`.

## Actualizador de Tauri: clave y manifiestos

`tauri.conf.json` → `plugins.updater.pubkey` lleva el placeholder literal
`__TAURI_UPDATER_PUBKEY__`. El pipeline (`safent-desktop.yml`, otro lane) debe:

1. Sustituir ese placeholder por la clave pública minisign real ANTES de
   `tauri build` (un `sed`/paso de template — la clave privada
   `TAURI_SIGNING_PRIVATE_KEY` es el secreto que ya existe en `agents-autonomy`,
   reutilizado, no uno nuevo).
2. Publicar `latest.json` (`includeUpdaterJson: true`) en
   `https://github.com/devwspito/safent-runtime/releases/latest/download/latest.json`
   (ya es el endpoint fijado en `plugins.updater.endpoints`).
3. Publicar `runtime-manifest.json` **firmado con la MISMA clave** junto a
   `latest.json` — `desktop/src-tauri/src/update/tauri_updater.rs`
   (`RuntimeManifestVerifier`) lo verifica con el mismo pubkey embebido.

`update/` (T014) ya trae: `plan_update` puro (regla "botón sólo si hay
novedad", probada con los tres casos — sólo app, sólo motor/compañero, los
tres a la vez, y el caso "versión legible cambia pero el digest no" que NO debe
generar plan); `run_update` (orquestador de los 11 pasos de
`contracts/update.md` §4, reversión automática entre `apply_engine` y
`verify_ready`, probada con dobles de test — feliz, cada punto de fallo con
reversión, y el caso "la propia reversión también falla" con un resultado
tipado en vez de silencioso); verificación minisign de `runtime-manifest.json`
con una firma real generada con `minisign -G`/`-S` (no un fixture inventado).
24 tests, `cargo test` limpio. Falta cablear `run_update`/`UpdatePorts` al CLI
embebido real — eso es integración de T011, no de esta entrega.
