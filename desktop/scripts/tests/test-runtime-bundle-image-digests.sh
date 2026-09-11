#!/usr/bin/env bash
# test-runtime-bundle-image-digests.sh — MAC-03 (verificacion-mac-1.md):
# _write_runtime_bundle_manifest must carry engine_image/companion_image
# straight from runtime-manifest.lock into the staged runtime-bundle.json,
# so boot.rs/selftest.rs can read a real digest instead of requiring
# SAFENT_ENGINE_DIGEST (an env var nothing in the real pipeline ever sets).
# Isolated: extracts ONLY that one function's real source (never sourced
# whole — the rest of stage-runtime.sh's top-level code downloads real
# podman over the network for a real target) and exercises it against
# synthetic fixtures. No network, no container (Constitution Principle V).
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"

fail() { echo "[x] $*" >&2; exit 1; }
pass() { echo "[ok] $*"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

# SHA256() lives in lib/fetch-verified.sh; _write_runtime_bundle_manifest
# calls it directly.
# shellcheck source=../lib/fetch-verified.sh
source "$SCRIPTS_DIR/lib/fetch-verified.sh"

# Extract CODESIGN=/_is_macho()/_write_runtime_bundle_manifest() as ONE
# contiguous fragment — MAC-02 added a call from the latter to _is_macho,
# so this test needs it defined too, or it silently degrades (an
# undefined _is_macho just makes every `if _is_macho "$f"` false under
# `set -e`'s if-condition exemption, no error, no cdhash — irrelevant to
# THIS test's own image_engine/companion_image assertions, but wrong).
# Stops at the first column-0 `}` seen AFTER _write_runtime_bundle_
# manifest's own opening line, regardless of exact line numbers.
FUNC_SRC="$(awk '
  /^CODESIGN=/ { printing=1 }
  printing { print }
  /^_write_runtime_bundle_manifest\(\) \{$/ { in_target=1 }
  in_target && /^}$/ { exit }
' "$SCRIPTS_DIR/stage-runtime.sh")"
[ -n "$FUNC_SRC" ] || fail "could not extract the CODESIGN/_is_macho/_write_runtime_bundle_manifest fragment from stage-runtime.sh — did it get renamed?"
eval "$FUNC_SRC"

run_case() {
  local label="$1" lockfile_body="$2"
  local dest="$WORK/$label/dest"
  mkdir -p "$dest"
  echo "dummy podman binary" > "$dest/podman"
  echo "dummy safent script" > "$dest/safent"
  local lockfile="$WORK/$label/runtime-manifest.lock"
  printf '%s' "$lockfile_body" > "$lockfile"

  # The variables _write_runtime_bundle_manifest reads directly (no
  # parameters — matches how stage-runtime.sh's own top level calls it).
  TARGET="aarch64-unknown-linux-gnu" LOCKFILE="$lockfile" DEST="$dest" _write_runtime_bundle_manifest >/dev/null

  echo "$dest/runtime-bundle.json"
}

# Case 1: both engine and companion digests pinned.
out="$(run_case "both-pinned" '{
  "targets": {"aarch64-unknown-linux-gnu": {"podman_version": "6.1.1"}},
  "engine_image": {"repo": "ghcr.io/devwspito/safent", "digest": "sha256:engine-good"},
  "companion_image": {"repo": "ghcr.io/devwspito/safent-ads", "digest": "sha256:ads-good"}
}')"
[ "$(jq -r '.engine_image.repo' "$out")" = "ghcr.io/devwspito/safent" ] || fail "engine_image.repo not propagated (both-pinned)"
[ "$(jq -r '.engine_image.digest' "$out")" = "sha256:engine-good" ] || fail "engine_image.digest not propagated (both-pinned)"
[ "$(jq -r '.companion_image.repo' "$out")" = "ghcr.io/devwspito/safent-ads" ] || fail "companion_image.repo not propagated (both-pinned)"
[ "$(jq -r '.companion_image.digest' "$out")" = "sha256:ads-good" ] || fail "companion_image.digest not propagated (both-pinned)"
pass "both engine_image and companion_image propagate repo+digest verbatim"

# Case 2: engine pinned, digest not yet fixed (null) — a legitimate
# "release pipeline has not run yet" state, must ship through as null, not
# be dropped or turned into the string "null".
out="$(run_case "null-digest" '{
  "targets": {"aarch64-unknown-linux-gnu": {"podman_version": "6.1.1"}},
  "engine_image": {"repo": "ghcr.io/devwspito/safent", "digest": null}
}')"
[ "$(jq -r '.engine_image.repo' "$out")" = "ghcr.io/devwspito/safent" ] || fail "engine_image.repo not propagated (null-digest)"
[ "$(jq '.engine_image.digest' "$out")" = "null" ] || fail "engine_image.digest must ship as JSON null, not a string or absent (null-digest)"
[ "$(jq '.companion_image' "$out")" = "null" ] || fail "companion_image must be null when absent from the lock (null-digest)"
pass "a null digest ships through as JSON null, not dropped or stringified"

# Case 3: neither field present in the lock at all (an OLDER lock, or one
# never touched by this pass) — must still produce a valid manifest with
# explicit nulls, never a missing key or a jq error.
out="$(run_case "no-image-fields" '{
  "targets": {"aarch64-unknown-linux-gnu": {"podman_version": "6.1.1"}}
}')"
[ "$(jq '.engine_image' "$out")" = "null" ] || fail "engine_image must be null when the lock has no such key at all (no-image-fields)"
[ "$(jq '.companion_image' "$out")" = "null" ] || fail "companion_image must be null when the lock has no such key at all (no-image-fields)"
[ "$(jq '.entries | length' "$out")" = "2" ] || fail "the two dummy staged files must still be recorded in entries (no-image-fields)"
pass "an older lock with neither field still produces a valid manifest (explicit nulls, no crash)"

echo "[ok] test-runtime-bundle-image-digests.sh: all cases passed"
