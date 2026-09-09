#!/usr/bin/env bash
# provision.sh — one-time-idempotent host-side provisioning of the safent-ads
# companion (024, plan.md §1.1). Invoked by run-safent.sh BEFORE the Safent
# container starts, and by `safent update` on every re-provision.
#
# Product constants (spec.md §7) — NEVER re-chosen at runtime. If the subnet
# or port is already taken on this host, provisioning FAILS LOUD and Safent
# starts WITHOUT the companion (FR-3/FR-6) — it never silently picks another
# subnet/port (owner decision 2).
set -euo pipefail

readonly COMPANION_SUBNET="10.201.0.0/24"
readonly COMPANION_GATEWAY="10.201.0.1"
readonly COMPANION_IP="10.201.0.10"
readonly COMPANION_PORT="8443"
readonly COMPANION_HOST="ads.safent.internal"
readonly COMPANION_NETWORK="safent-companions"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="${SAFENT_COMPANION_STATE:-$HOME/.safent/companions/ads}"
RUNTIME="$(command -v podman || command -v docker)"
[ -n "$RUNTIME" ] || { echo "provision.sh: need podman or docker" >&2; exit 1; }

mkdir -p "$STATE/tls" "$STATE/secrets"
chmod 0700 "$STATE"

log() { echo "[companion:ads] $*"; }
fail() { echo "[companion:ads] FALLO: $*" >&2; exit 1; }

# ── 1. Network — FIXED subnet, fail loud on conflict (owner decision 2) ─────
ensure_network() {
  local existing_subnet
  existing_subnet="$("$RUNTIME" network inspect "$COMPANION_NETWORK" \
    --format '{{(index .Subnets 0).Subnet}}' 2>/dev/null || true)"
  if [ -n "$existing_subnet" ]; then
    [ "$existing_subnet" = "$COMPANION_SUBNET" ] || fail \
      "la red '$COMPANION_NETWORK' ya existe con otra subred ($existing_subnet) — bórrala a mano si quieres reprovisionar"
    log "red '$COMPANION_NETWORK' ya existe ($COMPANION_SUBNET) — OK"
    return 0
  fi
  if ! "$RUNTIME" network create "$COMPANION_NETWORK" \
        --subnet "$COMPANION_SUBNET" --gateway "$COMPANION_GATEWAY" >/dev/null 2>&1; then
    fail "'$COMPANION_SUBNET' ya está en uso por otra red de este host — libérala o el companion no se instala (nunca elegimos otra)"
  fi
  log "red '$COMPANION_NETWORK' creada ($COMPANION_SUBNET)"
}

# ── 2. CA + leaf (ECDSA P-256, SAN=ads.safent.internal) ─────────────────────
ensure_tls() {
  [ -f "$STATE/tls/ca.crt" ] && [ -f "$STATE/tls/leaf.crt" ] && [ -f "$STATE/tls/leaf.key" ] && return 0
  log "generando CA privada + hoja TLS para $COMPANION_HOST…"
  local ca_key="$STATE/tls/ca.key" ca_crt="$STATE/tls/ca.crt"
  local leaf_key="$STATE/tls/leaf.key" leaf_crt="$STATE/tls/leaf.crt"
  openssl ecparam -genkey -name prime256v1 -noout -out "$ca_key"
  openssl req -x509 -new -key "$ca_key" -days 3650 -sha256 -subj "/CN=Safent Ads companion CA" -out "$ca_crt"
  openssl ecparam -genkey -name prime256v1 -noout -out "$leaf_key"
  openssl req -new -key "$leaf_key" -subj "/CN=$COMPANION_HOST" \
    -addext "subjectAltName=DNS:$COMPANION_HOST" -out "$STATE/tls/leaf.csr"
  openssl x509 -req -in "$STATE/tls/leaf.csr" -CA "$ca_crt" -CAkey "$ca_key" -CAcreateserial \
    -days 825 -sha256 -extfile <(printf 'subjectAltName=DNS:%s' "$COMPANION_HOST") -out "$leaf_crt"
  rm -f "$STATE/tls/leaf.csr"
  chmod 0600 "$ca_key" "$leaf_key"
  chmod 0644 "$ca_crt" "$leaf_crt"
}

# ── 3. Bearer ────────────────────────────────────────────────────────────────
ensure_bearer() {
  [ -f "$STATE/bearer" ] && return 0
  openssl rand -hex 32 > "$STATE/bearer"
  chmod 0400 "$STATE/bearer"
  log "bearer generado"
}

# ── 4. companions.json (exact shape hermes.shell_server.companions validates) ─
write_companions_json() {
  local fingerprint
  fingerprint="sha256:$(openssl x509 -in "$STATE/tls/ca.crt" -outform der | sha256sum | cut -d' ' -f1)"
  # Written to a temp name first: the live file is 0444 and (when we could
  # chown it) root-owned, so `cat >` onto it would fail — rename in the
  # owner-writable $STATE dir is the only re-provision path that works.
  cat > "$STATE/companions.json.tmp" <<JSON
{"version": 1, "companions": [{
  "slug": "safent-ads",
  "url": "https://$COMPANION_HOST:$COMPANION_PORT/mcp",
  "ip": "$COMPANION_IP",
  "port": $COMPANION_PORT,
  "ca_path": "/etc/hermes/companions/ads-ca.crt",
  "ca_fingerprint": "$fingerprint",
  "bearer_ref": "file:/etc/hermes/companions/ads.bearer"}]}
JSON
  # 0444 + root:root is the shape hermes.shell_server.companions accepts
  # unconditionally. Rootless podman/docker remap us to uid 0 inside the
  # container so the chown is cosmetic there; ROOTFUL podman does not remap
  # at all, and without it the file arrives as uid 1000 — the loader then
  # relies on its second branch (the `:ro` bind mount), which we always
  # provide from run-safent.sh. Never fail provisioning over the chown: the
  # 0444 mode + read-only mount already carry the invariant.
  chmod 0444 "$STATE/companions.json.tmp"
  if [ "$(id -u)" -eq 0 ]; then
    chown 0:0 "$STATE/companions.json.tmp"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo -n chown 0:0 "$STATE/companions.json.tmp" || \
      log "sin privilegios para chown root:root — vale igual (bind :ro + 0444)"
  else
    log "sin sudo no interactivo — companions.json queda 0444 de tu usuario (bind :ro lo protege)"
  fi
  mv -f "$STATE/companions.json.tmp" "$STATE/companions.json"
}

# ── 5. secrets/api.env for ads-api/ads-worker + up ───────────────────────────
ensure_compose_secrets() {
  [ -f "$STATE/secrets/api.env" ] || : > "$STATE/secrets/api.env"
  [ -f "$STATE/pg_password" ] || openssl rand -hex 32 > "$STATE/pg_password"
  chmod 0400 "$STATE/pg_password"
}

start_companion() {
  export SAFENT_STATE="$STATE"
  export ADS_POSTGRES_PASSWORD
  ADS_POSTGRES_PASSWORD="$(cat "$STATE/pg_password")"
  "$RUNTIME" compose -p safent-ads -f "$HERE/compose.yaml" up -d
}

# ── 6. Wait for /mcp/health (best-effort — see this feature's report: the
# ads image doesn't expose it yet, so this simply times out today without
# blocking Safent's own boot, matching FR-3). ────────────────────────────────
wait_for_health() {
  local i=0
  while [ $i -lt 60 ]; do
    if curl -fsS --max-time 2 --cacert "$STATE/tls/ca.crt" \
        "https://$COMPANION_IP:$COMPANION_PORT/mcp/health" >/dev/null 2>&1; then
      log "companion listo"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  log "companion aún no responde /mcp/health (Safent arranca igual — FR-3)"
}

ensure_network
ensure_tls
ensure_bearer
write_companions_json
ensure_compose_secrets
start_companion
wait_for_health
log "aprovisionamiento OK — $STATE/companions.json listo para el bind read-only"
