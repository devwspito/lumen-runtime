#!/usr/bin/env node
/**
 * Lumen launcher — `npx @devwspito/lumen`
 *
 * Pulls the hardened Lumen container image, runs it with the secure-by-default
 * flags (the same posture as ops/container/run-lumen.sh), waits for the daemon,
 * then opens the browser at the auto-minted, per-boot, unique bootstrap URL.
 *
 * Everything else — model/provider, Composio, Brave, agents, skills — is
 * configured IN THE UI. No env files, no secrets on disk.
 */
'use strict'

const { execSync, spawnSync } = require('node:child_process')
const path = require('node:path')

const IMAGE = process.env.LUMEN_IMAGE || 'ghcr.io/devwspito/lumen:latest'
const PORT = process.env.LUMEN_PORT || '17517'
const NAME = process.env.LUMEN_NAME || 'lumen'
const SECCOMP = path.join(__dirname, 'lumen.json')

function sh(cmd) {
  return execSync(cmd, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
}
function which(bin) {
  try { return sh(`command -v ${bin}`) } catch { return '' }
}
function sleep(sec) {
  try { spawnSync('sleep', [String(sec)], { stdio: 'ignore' }) } catch { /* noop */ }
}

const runtime = which('podman') || which('docker')
if (!runtime) {
  console.error('✗ Necesitas podman o docker.  →  https://podman.io/get-started')
  process.exit(1)
}
const rt = path.basename(runtime)

// macOS: the cage (Landlock/netns) needs a ROOTFUL podman machine.
if (rt === 'podman') {
  let rootful = 'unknown'
  try { rootful = sh('podman machine inspect --format "{{.Rootful}}"') } catch { rootful = 'nomachine' }
  if (rootful === 'nomachine') {
    console.error('✗ No hay una podman machine. Crea una (rootful):')
    console.error('    podman machine init --rootful --cpus 4 --memory 8192 --disk-size 60')
    console.error('    podman machine start')
    process.exit(1)
  }
  if (rootful === 'false') {
    console.error('✗ La podman machine es rootless; la jaula necesita rootful:')
    console.error('    podman machine stop && podman machine set --rootful && podman machine start')
    process.exit(1)
  }
}

console.log('▸ Descargando Lumen…')
if (spawnSync(runtime, ['pull', IMAGE], { stdio: 'inherit' }).status !== 0) {
  console.error(`✗ No se pudo descargar la imagen ${IMAGE}.`)
  process.exit(1)
}

console.log('▸ Arrancando…')
spawnSync(runtime, ['rm', '-f', NAME], { stdio: 'ignore' })
const runArgs = [
  'run', '-d', '--name', NAME, '--systemd=always',
  '-p', `127.0.0.1:${PORT}:7517`,
  '--cap-add', 'NET_ADMIN', '--cap-add', 'SYS_ADMIN', '--cap-add', 'AUDIT_READ',
  '--security-opt', `seccomp=${SECCOMP}`,
  '--security-opt', 'unmask=/sys/kernel/security',
  '--security-opt', 'label=disable',
  '-v', '/sys/kernel/security:/sys/kernel/security:ro',
  '-v', 'lumen-data:/var/lib/hermes',
  '--shm-size=1g',
  IMAGE,
]
if (spawnSync(runtime, runArgs, { stdio: 'inherit' }).status !== 0) {
  console.error('✗ No se pudo arrancar el contenedor.')
  process.exit(1)
}

console.log('▸ Esperando a Lumen…')
let secret = ''
for (let i = 0; i < 48; i++) {
  let active = ''
  try { active = sh(`${runtime} exec ${NAME} systemctl is-active hermes-runtime`) } catch { /* booting */ }
  if (active === 'active') {
    try {
      secret = sh(`${runtime} exec ${NAME} cat /var/lib/hermes-bootstrap/bootstrap/webui-bootstrap`).replace(/\r?\n/g, '')
    } catch { /* not ready */ }
    if (secret) break
  }
  if (active === 'failed') break
  sleep(5)
}

if (!secret) {
  console.error(`\n  ⚠ Lumen arrancó pero no obtuve el token. Mira:  ${runtime} logs ${NAME}`)
  process.exit(1)
}

const url = `http://localhost:${PORT}/?k=${secret}`
console.log(`\n  ✅ Lumen está listo:\n     ${url}\n`)
console.log('     (El modelo, Composio, Brave y todo lo demás se configuran en la UI.)')

// Open the default browser. The ?k= token is unique per boot — it never persists.
const opener = process.platform === 'darwin' ? 'open'
  : process.platform === 'win32' ? 'cmd' : 'xdg-open'
const openerArgs = process.platform === 'win32' ? ['/c', 'start', '', url] : [url]
try { spawnSync(opener, openerArgs, { stdio: 'ignore' }) } catch { /* user opens it manually */ }
