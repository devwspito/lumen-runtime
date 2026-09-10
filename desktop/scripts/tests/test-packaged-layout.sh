#!/usr/bin/env bash
# test-packaged-layout.sh — proves desktop/src-tauri/resources/runtime/ (the
# tree tauri.bundle.conf.json's `bundle.resources` glob sweeps whole into the
# .app) contains ONLY extracted, final files — never a raw download archive,
# a partial download, or a cache directory.
#
# Exists because of a REAL macOS notarization rejection (notarytool log,
# validation #12): every rejection pointed at binaries INSIDE
# "Safent.app/Contents/Resources/runtime/krunkit-podman-unsigned-1.3.2.tgz/..."
# — the raw .tgz itself had been staged under resources/runtime/ (in a
# gitignored .cache/ subdir, which does NOT stop tauri-bundler's glob from
# matching it: `resources/runtime/*/**/*`'s first `*` matches a dotdir like
# any other name) and got bundled + inspected alongside the legitimate,
# signed, extracted copies of the same binaries.
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
DESKTOP_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
RESOURCES_ROOT="$DESKTOP_DIR/src-tauri/resources/runtime"
TARGET="${1:-aarch64-unknown-linux-gnu}"

fail() {
  echo "[x] FAIL: $1" >&2
  exit 1
}

echo "[*] staging $TARGET for real (proves this against an actual run, not a synthetic fixture)"
bash "$SCRIPTS_DIR/stage-runtime.sh" "$TARGET"

DEST="$RESOURCES_ROOT/$TARGET"
[ -d "$DEST" ] || fail "expected $DEST to exist after staging"

echo "[*] no archive extensions anywhere under resources/runtime/"
FORBIDDEN_PATTERNS=('*.tgz' '*.tar.gz' '*.zip' '*.pkg' '*.part' '*.verified')
found=0
for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  while IFS= read -r -d '' hit; do
    echo "    forbidden: $hit (matches $pattern)" >&2
    found=1
  done < <(find "$RESOURCES_ROOT" -iname "$pattern" -print0)
done
[ "$found" -eq 0 ] || fail "found archive/partial/marker file(s) under $RESOURCES_ROOT — these get bundled and break notarization"

echo "[*] no .cache (or any dotdir) anywhere under resources/runtime/"
while IFS= read -r -d '' d; do
  fail "dotdir under resources/runtime/: $d — bundle.resources' glob matches dot-entries too, this gets bundled"
done < <(find "$RESOURCES_ROOT" -type d -name '.*' -print0)

echo "[*] the cache dir actually moved out — the real download archive exists OUTSIDE resources/runtime/"
CACHE_DIR="$DESKTOP_DIR/.cache/runtime-downloads/$TARGET"
[ -d "$CACHE_DIR" ] || fail "expected a cache dir at $CACHE_DIR — did CACHE_DIR move back under resources/runtime/?"
cache_file_count="$(find "$CACHE_DIR" -type f | wc -l | tr -d ' ')"
[ "$cache_file_count" -gt 0 ] || fail "expected at least one cached archive under $CACHE_DIR"
case "$CACHE_DIR" in
  "$RESOURCES_ROOT"*) fail "CACHE_DIR ($CACHE_DIR) is under resources/runtime/ ($RESOURCES_ROOT) — this is exactly the bug" ;;
esac
echo "    cache dir: $CACHE_DIR ($cache_file_count file(s), outside resources/runtime/, confirmed)"

echo "[ok] packaged layout is clean: resources/runtime/ has no archives/partials/dotdirs, cache lives outside it"
