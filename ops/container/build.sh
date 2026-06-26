#!/usr/bin/env bash
# build.sh — build the CE and/or EE image tags from the SAME Containerfile.
#
# Usage:
#   ./ops/container/build.sh            # builds CE tag (default)
#   ./ops/container/build.sh --ee       # builds EE tag
#   ./ops/container/build.sh --all      # builds both CE and EE
#   ./ops/container/build.sh --push     # push after building (requires registry auth)
#
# The Containerfile accepts ARG LUMEN_EDITION=community (default) | enterprise.
# Both tags share the same base layers; only the edition marker file and the
# systemctl-enable for hermes-config-sync differ, so the EE build reuses the CE
# layer cache and is fast.
#
# DO NOT run builds from CI/CD as a developer — the pipeline owns publishing.
# This script is for local validation only.
set -euo pipefail

RUNTIME="$(command -v podman 2>/dev/null || command -v docker 2>/dev/null || true)"
[ -n "$RUNTIME" ] || { echo "[x] need podman or docker"; exit 1; }

HERE="$(cd "$(dirname "$0")/../.." && pwd)"   # repo root
cd "$HERE"

CE_TAG="${CE_TAG:-ghcr.io/devwspito/lumen:latest}"
EE_TAG="${EE_TAG:-ghcr.io/devwspito/lumen-enterprise:latest}"
FE_CACHEBUST="${FE_CACHEBUST:-$(date +%s)}"

BUILD_CE=false
BUILD_EE=false
DO_PUSH=false

for arg in "$@"; do
  case "$arg" in
    --ee)   BUILD_EE=true ;;
    --all)  BUILD_CE=true; BUILD_EE=true ;;
    --push) DO_PUSH=true ;;
    *)      echo "[x] unknown flag: $arg"; exit 1 ;;
  esac
done

# Default: CE only
[ "$BUILD_CE" = false ] && [ "$BUILD_EE" = false ] && BUILD_CE=true

_build() {
  local edition="$1" tag="$2"
  echo "[*] Building ${edition} → ${tag}"
  "$RUNTIME" build \
    --build-arg LUMEN_EDITION="${edition}" \
    --build-arg FE_CACHEBUST="${FE_CACHEBUST}" \
    -f ops/container/Containerfile \
    -t "${tag}" .
  echo "[ok] ${tag} built"
  if [ "$DO_PUSH" = true ]; then
    echo "[*] Pushing ${tag}..."
    "$RUNTIME" push "${tag}"
    echo "[ok] ${tag} pushed"
  fi
}

[ "$BUILD_CE" = true ] && _build community "$CE_TAG"
[ "$BUILD_EE" = true ] && _build enterprise "$EE_TAG"
