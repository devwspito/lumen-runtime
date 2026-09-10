#!/usr/bin/env bash
# test-packaged-layout.sh — packaged-layout contract test (packaging review
# item 1, specs/028-safent-app-nativa/verificacion-paquete-linux.md
# "Comprobación 2"): asserts the EXACT set of files + hashes stage-runtime.sh
# produces under resources/runtime/<triple>/ includes the safent CLI + host
# launcher + companion provisioning assets, not just podman — the concrete
# regression a real signed package shipped with (the CLI was in NO package;
# `boot.rs::resolve_config` had nothing to invoke).
#
# Runs the REAL stage-runtime.sh for the CURRENT host's own Linux triple
# (network-touching — same as a real CI matrix leg; skips outright on a
# non-Linux host or an unsupported arch instead of guessing).
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
DESKTOP_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
LOCKFILE="$DESKTOP_DIR/runtime-manifest.lock"

fail() { echo "[x] $*" >&2; exit 1; }
pass() { echo "[ok] $*"; }

case "$(uname -s)" in
  Linux) ;;
  *) echo "[skip] test-packaged-layout.sh only covers Linux triples on this host ($(uname -s))"; exit 0 ;;
esac
case "$(uname -m)" in
  aarch64) TARGET=aarch64-unknown-linux-gnu ;;
  x86_64)  TARGET=x86_64-unknown-linux-gnu ;;
  *) echo "[skip] unsupported arch for this test: $(uname -m)"; exit 0 ;;
esac

DEST="$DESKTOP_DIR/src-tauri/resources/runtime/$TARGET"
MANIFEST="$DEST/runtime-bundle.json"

"$SCRIPTS_DIR/stage-runtime.sh" "$TARGET" >&2

[ -f "$MANIFEST" ] || fail "no runtime-bundle.json under $DEST after staging"

# 1. The exact five app files (the regression this test guards) must be
#    present, executable where expected, and hash-match BOTH records:
#    runtime-bundle.json (what cmd_stage_runtime verifies at the owner's
#    first real run) and runtime-manifest.lock's .app_files (provenance,
#    recomputed from this same checkout).
EXPECTED_APP_FILES="safent run-safent.sh provision.sh compose.yaml caps.template.yaml"
for name in $EXPECTED_APP_FILES; do
  [ -f "$DEST/$name" ] || fail "expected app file missing from staged tree: $name"

  bundle_sha="$(jq -r --arg p "$name" '.entries[] | select(.path == $p) | .sha256' "$MANIFEST")"
  [ -n "$bundle_sha" ] || fail "runtime-bundle.json has no entry for $name"
  got_sha="$(sha256sum "$DEST/$name" | awk '{print $1}')"
  [ "$got_sha" = "$bundle_sha" ] || fail "$name: staged file does not match its own runtime-bundle.json entry"

  lock_sha="$(jq -r --arg n "$name" '.app_files.entries[] | select(.flat_name == $n) | .sha256' "$LOCKFILE")"
  [ -n "$lock_sha" ] || fail "runtime-manifest.lock .app_files has no entry for $name"
  [ "$lock_sha" = "$got_sha" ] || fail "$name: staged sha256 disagrees with runtime-manifest.lock's .app_files record"
done
pass "all 5 app files present, executable-flagged where required, hash-matched in both records: $EXPECTED_APP_FILES"

# 2. safent/run-safent.sh/provision.sh must be individually executable
#    (0755) — a non-executable CLI is exactly as useless as a missing one.
for name in safent run-safent.sh provision.sh; do
  mode="$(jq -r --arg p "$name" '.entries[] | select(.path == $p) | .mode' "$MANIFEST")"
  [ "$mode" = "0755" ] || fail "$name: runtime-bundle.json records mode $mode, want 0755"
  [ -x "$DEST/$name" ] || fail "$name: staged file is not actually executable on disk"
done
pass "safent/run-safent.sh/provision.sh are 0755 in both the manifest and on disk"

# 3. The pinned podman toolchain from runtime-manifest.lock's .targets[] is
#    STILL there too — this test guards the NEW files, not at the expense of
#    the existing ones. Staged tree is still NESTED (bin/libexec/etc/) —
#    Tauri's own bundle.resources glob is what flattens it at package time —
#    so presence on disk is checked at the lock's own (nested) path, while
#    runtime-bundle.json's RECORD of it (what cmd_stage_runtime trusts) is
#    checked by the post-flatten flat basename.
n="$(jq -r --arg t "$TARGET" '.targets[$t].entries | length' "$LOCKFILE")"
i=0
while [ "$i" -lt "$n" ]; do
  path="$(jq -r --arg t "$TARGET" ".targets[\$t].entries[$i].path" "$LOCKFILE")"
  want_sha="$(jq -r --arg t "$TARGET" ".targets[\$t].entries[$i].sha256" "$LOCKFILE")"
  base="$(basename "$path")"
  [ -f "$DEST/$path" ] || fail "podman toolchain file missing from staged tree: $path"
  if [ "$path" = "etc/containers/containers.conf" ]; then
    # Deliberately patched post-verification (stage-runtime.sh's own
    # _patch_bundled_containers_conf, packaging review item 3: lock_type =
    # "file" so the bundled podman never collides with the host's own on
    # /dev/shm) — its lock entry pins the PRISTINE upstream download on
    # purpose, so the staged (patched) file never matches it again; same
    # special case as _already_staged() itself.
    grep -q '^lock_type = "file"' "$DEST/$path" || fail "$path: not patched with lock_type"
    i=$((i + 1))
    continue
  fi
  got_sha="$(sha256sum "$DEST/$path" | awk '{print $1}')"
  [ "$got_sha" = "$want_sha" ] || fail "$path: staged sha256 disagrees with runtime-manifest.lock"
  bundle_sha="$(jq -r --arg p "$base" '.entries[] | select(.path == $p) | .sha256' "$MANIFEST")"
  [ "$bundle_sha" = "$want_sha" ] || fail "$base: runtime-bundle.json's flattened record disagrees with runtime-manifest.lock"
  i=$((i + 1))
done
pass "all $n pinned podman toolchain entries from runtime-manifest.lock present (nested on disk, flattened in runtime-bundle.json)"

# 4. Every entry runtime-bundle.json claims must be exactly what cmd_stage_runtime
#    would later verify+copy — total entry count is podman entries + pasta
#    symlink-as-copy + the 5 app files (no extras, nothing silently dropped).
total="$(jq '.entries | length' "$MANIFEST")"
want_total=$((n + 1 + 5)) # +1 for bin/pasta (staged as a real copy, not counted in .targets[].entries)
[ "$total" -eq "$want_total" ] || fail "runtime-bundle.json has $total entries, want $want_total ($n podman + 1 pasta + 5 app files)"
pass "runtime-bundle.json entry count matches exactly: $total"

echo "[ok] test-packaged-layout.sh: packaged layout for $TARGET is contract-complete"
