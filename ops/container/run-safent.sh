#!/usr/bin/env bash
# run-safent.sh — canonical HARDENED launch for the Safent standard container.
#
# This is the secure-by-default posture validated by the red-team (penetrate +
# escape). The desktop wrapper / OSS users should launch with THESE flags — not a
# bare `docker run`. See SECURITY.md for what each flag enforces and the host
# requirements (a Landlock-capable kernel).
#
#   ./run-safent.sh [IMAGE] [HOST_PORT] [--codex-auth <path-to-auth.json>]
#   ./run-safent.sh --help
#
# --codex-auth <path>: OPTIONAL. Bind-mounts an EXISTING, host-side OpenAI
#   Codex CLI auth.json (from a `codex login` the owner already did on the
#   HOST) read-only into the container's CODEX_HOME, so the OPT-IN
#   `codex_app_server` runtime (hermes-agent 0.15.1, agent/transports/
#   codex_app_server.py — spawns the REAL `codex` binary, which reads
#   CODEX_HOME/auth.json itself) can reuse that session without a second
#   login. This is a SECONDARY path for that opt-in runtime only — it needs
#   no container flag for either of the two normal OpenAI Codex / ChatGPT
#   provider paths below (both run entirely from inside Safent's own UI,
#   Settings -> Providers -> OpenAI Codex / ChatGPT (suscripción)):
#     1. ChatGPT subscription, device-code login (the default: click "Iniciar
#        sesión con ChatGPT" — dbus_runtime_service.py's _codex_oauth_worker).
#     2. Your own OpenAI API key, pay-per-token fallback ("Usar clave de API
#        en su lugar" on the same card — plan.md D-A4).
#
# Safent Ads (MCP campaign tools, Google/Meta) is set up the SAME way, no
# container flag either: Herramientas -> "Safent Ads · campañas Google/Meta"
# -> paste your tenant's https:// MCP URL -> Conectar. That single URL is the
# owner-authorized managed-remote endpoint (hermes.shell_server.
# managed_remote_endpoints; https-only, no IP literals, port 443 only) the
# container's default-deny MCP netns is allowed to reach for that ONE bridge.
set -euo pipefail

IMAGE="ghcr.io/devwspito/safent:latest"
HOST_PORT="17517"
CODEX_AUTH_PATH=""
_positional_index=0

usage() {
  sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --codex-auth)
      [ $# -ge 2 ] || { echo "--codex-auth requires a path"; exit 1; }
      CODEX_AUTH_PATH="$2"
      shift 2
      ;;
    --codex-auth=*)
      CODEX_AUTH_PATH="${1#*=}"
      shift
      ;;
    *)
      case "$_positional_index" in
        0) IMAGE="$1" ;;
        1) HOST_PORT="$1" ;;
        *) echo "unexpected argument: $1"; exit 1 ;;
      esac
      _positional_index=$((_positional_index + 1))
      shift
      ;;
  esac
done

NAME="${SAFENT_NAME:-safent}"
RUNTIME="$(command -v podman || command -v docker)"
HERE="$(cd "$(dirname "$0")" && pwd)"
SECCOMP="${SAFENT_SECCOMP:-$HERE/seccomp/safent.json}"

[ -n "$RUNTIME" ] || { echo "need podman or docker"; exit 1; }
[ -f "$SECCOMP" ] || { echo "seccomp profile not found: $SECCOMP"; exit 1; }

# CODEX_HOME lives inside the ALREADY-mounted safent-data volume (HERMES_HOME
# is /var/lib/hermes/hermes-home — see ops/agents-os-edition/systemd/hermes-
# runtime.service) so it persists across image updates like every other
# credential. Read-only: the container never writes back to the host's file.
CODEX_AUTH_MOUNT=()
if [ -n "$CODEX_AUTH_PATH" ]; then
  [ -f "$CODEX_AUTH_PATH" ] || { echo "codex auth file not found: $CODEX_AUTH_PATH"; exit 1; }
  CODEX_AUTH_MOUNT=(
    -v "${CODEX_AUTH_PATH}:/var/lib/hermes/hermes-home/.codex/auth.json:ro"
    -e "CODEX_HOME=/var/lib/hermes/hermes-home/.codex"
  )
fi

"$RUNTIME" rm -f "$NAME" >/dev/null 2>&1 || true

# Timezone: the container must reason/schedule in the SAME wall-clock as the host
# that runs it — otherwise it defaults to UTC and the agent tells you the wrong
# time (e.g. "it's 11 PM" when your clock says 1 AM). Resolve the host IANA zone:
#   1. an explicit SAFENT_TZ / TZ wins (override for remote/headless installs),
#   2. else read the /etc/localtime symlink (works on macOS and Linux),
#   3. else fall back to UTC.
host_tz() {
  if [ -n "${SAFENT_TZ:-}" ]; then printf '%s' "$SAFENT_TZ"; return; fi
  if [ -n "${TZ:-}" ]; then printf '%s' "$TZ"; return; fi
  local link
  link="$(readlink /etc/localtime 2>/dev/null || true)"
  case "$link" in
    */zoneinfo/*) printf '%s' "${link##*/zoneinfo/}" ;;
    *) printf 'UTC' ;;
  esac
}
SAFENT_TZ_VALUE="$(host_tz)"

# WHY each flag (see SECURITY.md):
#   -p 127.0.0.1:...    publish on host LOOPBACK only — the control plane never
#                       faces the LAN. (The HTTP edge also requires a Bearer token.)
#   --cap-add NET_ADMIN add ONLY the three caps the cage needs on top of podman's
#   --cap-add SYS_ADMIN default (already-reduced) set: NET_ADMIN (veth + nftables +
#   --cap-add AUDIT_READ netns), SYS_ADMIN (create the netns + transient units),
#                       AUDIT_READ (audit). NET_ADMIN is NOT in podman's default set
#                       (only NET_BIND_SERVICE is), so it must be explicit or the
#                       netns jail fails to build. We do NOT --cap-drop ALL: systemd
#                       PID1 + journald + dbus + keygen need the default baseline to
#                       boot. Least-privilege for the AGENT is enforced PER-UNIT
#                       (CapabilityBoundingSet= empty on the browser/exec/terminal
#                       units) + non-root uid 880 — not at the container level.
#                       NEVER --privileged (that re-opens container escape).
#   --security-opt seccomp=<profile>  kernel syscall backstop: allows landlock_*
#                       (so the browser FS jail loads) + denies mount/setns/ptrace/
#                       pivot_root (so a Chromium 0-day can't escape the netns).
#   --security-opt unmask=/sys/kernel/security  let hermes-landlock-assert read the
#                       LSM list (read-only) to fail-closed if Landlock is absent.
#   -v /sys/kernel/security:ro  expose securityfs read-only for the same check.
#   --security-opt label=disable  on SELinux-enforcing hosts (Fedora/RHEL, and the
#                       Fedora CoreOS VM that backs `podman machine` on macOS) SELinux
#                       denies the container reading securityfs → the Landlock assert
#                       wrongly sees "no Landlock" and fail-closes. Disabling the SELinux
#                       label for THIS container restores the read (the cage's real
#                       confinement is Landlock/seccomp/netns/uid inside, not the outer
#                       SELinux label). No-op on AppArmor/no-LSM hosts.
#   --shm-size=1g       Chromium needs a real /dev/shm.
#   -v safent-data       persist /var/lib/hermes (keystore, audit, config) across
#                       image updates (so master.key / provider keys survive pull).
# NOTE: NoNewPrivileges is set PER-UNIT (the hardened units), NOT container-wide —
# a container-level no-new-privileges breaks dbus/login setuid and the boot fails.
exec "$RUNTIME" run -d --name "$NAME" --systemd=always \
  -p "127.0.0.1:${HOST_PORT}:7517" \
  -e "TZ=${SAFENT_TZ_VALUE}" -e "HERMES_TZ=${SAFENT_TZ_VALUE}" \
  --cap-add NET_ADMIN --cap-add SYS_ADMIN --cap-add AUDIT_READ \
  --security-opt "seccomp=${SECCOMP}" \
  --security-opt unmask=/sys/kernel/security \
  --security-opt label=disable \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  -v safent-data:/var/lib/hermes \
  --shm-size=1g \
  ${CODEX_AUTH_MOUNT[@]+"${CODEX_AUTH_MOUNT[@]}"} \
  "$IMAGE"
