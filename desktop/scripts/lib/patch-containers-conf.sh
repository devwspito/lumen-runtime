#!/usr/bin/env bash
# patch-containers-conf.sh — one function, `patch_containers_conf_for_bundled_helpers`,
# the exact transformation stage-runtime.sh applies to the upstream
# podman-static containers.conf so the bundled podman finds ITS OWN bundled
# netavark/aardvark-dns/rootlessport (and uses pasta) instead of silently
# falling back to whatever happens to already be installed on the host.
# Sourced, never executed directly — kept separate from stage-runtime.sh so
# this transformation is testable on its own (tests/test-patch-containers-conf.sh).
#
# Confirmed on this DGX (2026-09-10, see runtime-manifest.lock's
# containers_conf_patch note and research.md for the full investigation):
# WITHOUT helper_binaries_dir, a bundled podman on a host that happens to
# already have podman installed uses THAT copy's netavark instead of the
# bundled one (observed: fell back to system netavark 1.4.0 instead of the
# bundled 2.1.0). This is exactly the "depends on what's already on the
# machine" failure the whole point of bundling exists to avoid.
set -uo pipefail

patch_containers_conf_for_bundled_helpers() {
  local conf="$1"
  [ -f "$conf" ] || { echo "[x] patch_containers_conf_for_bundled_helpers: not a file: $conf" >&2; return 1; }
  grep -q '^\[engine\]$' "$conf" || { echo "[x] patch_containers_conf_for_bundled_helpers: no [engine] section in $conf" >&2; return 1; }

  # $BINDIR is containers-common's OWN literal token (pkg/config/config.go,
  # FindHelperBinary) for "directory of the currently running podman binary",
  # resolved fresh at runtime via os.Executable() — NOT a shell variable, and
  # deliberately never bash-expanded here (single-quoted sed program).
  # shellcheck disable=SC2016
  sed -i '/^\[engine\]$/a helper_binaries_dir = ["$BINDIR/../libexec/podman", "$BINDIR"]' "$conf"

  cat >>"$conf" <<'EOF'

[network]
default_rootless_network_cmd = "pasta"
EOF
}
