#!/usr/bin/env bash
# stage-runtime.sh — download the PINNED podman runtime for one target and stage
# it into desktop/src-tauri/resources/runtime/<target>/, where Tauri's resource
# resolver (tauri::Manager::path().resource_dir()) makes it available to the app
# at runtime. This is a BUILD-TIME script (developer machine or CI runner) — not
# shipped to end users, unlike `safent`/`get-safent.sh` (kept POSIX sh for that
# reason). This one may assume bash + curl + jq + sha256sum/shasum + tar.
#
# Usage:
#   desktop/scripts/stage-runtime.sh <target>
#   target = a RUST TARGET TRIPLE — the SAME ones agents-autonomy/.github/
#   workflows/safent-desktop.yml already uses in its build matrix, so the
#   pipeline can call this script with no translation layer of its own:
#     aarch64-apple-darwin        macOS Apple Silicon (ships a VM machine image)
#     x86_64-unknown-linux-gnu    Linux x86_64 (no VM)
#     aarch64-unknown-linux-gnu   Linux arm64 (no VM)
#   x86_64-apple-darwin is a RECOGNIZED triple that is deliberately rejected
#   (see below) — everything else is an unrecognized triple, rejected loudly.
#
# Every URL + sha256 this script trusts comes from ONE committed file,
# desktop/runtime-manifest.lock (sibling of this script's parent dir, keyed by
# the SAME triples) — nothing is fetched from a floating "latest" endpoint. A
# downloaded byte that does not match its pinned sha256 is deleted and the
# script exits non-zero: a mismatched binary is NEVER staged, let alone
# executed (data-model.md RuntimeBundle invariant — this script is the FIRST
# of two checks; the app re-verifies at runtime before exec, see
# contracts/app-engine.md).
#
# aarch64-apple-darwin must run on a macOS host/runner: it expands the official
# podman .pkg with `pkgutil --expand-full` WITHOUT installing it (no
# `installer -pkg`, no admin password) and copies the binaries out. On Linux
# this step fails fast with a clear message instead of doing partial work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCKFILE="$DESKTOP_DIR/runtime-manifest.lock"
RESOURCES_ROOT="$DESKTOP_DIR/src-tauri/resources/runtime"

TARGET="${1:-}"
case "$TARGET" in
  aarch64-apple-darwin|x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu) ;;
  x86_64-apple-darwin)
    echo "[x] x86_64-apple-darwin: not staged on purpose — v6.1.1 publishes no" >&2
    echo "    macOS Intel installer. Matches contracts/update.md (darwin-x86_64" >&2
    echo "    intentionally absent). See runtime-manifest.lock ->" >&2
    echo "    excluded_targets.x86_64-apple-darwin." >&2
    exit 1
    ;;
  "")
    echo "usage: $0 <aarch64-apple-darwin|x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu>" >&2
    exit 1
    ;;
  *)
    echo "[x] unrecognized target triple: $TARGET" >&2
    echo "    want one of: aarch64-apple-darwin | x86_64-unknown-linux-gnu | aarch64-unknown-linux-gnu" >&2
    exit 1
    ;;
esac

for tool in curl jq tar; do
  command -v "$tool" >/dev/null 2>&1 || { echo "[x] need '$tool' on PATH (build-time only, not shipped)" >&2; exit 1; }
done
SHA256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'
  fi
}

[ -f "$LOCKFILE" ] || { echo "[x] missing $LOCKFILE"; exit 1; }
case "$TARGET" in
  *-apple-darwin)
    if [ "$(uname -s)" != Darwin ]; then
      echo "[x] $TARGET must be staged on a macOS host/runner (uses pkgutil to" >&2
      echo "    expand the official .pkg WITHOUT installing it). Refusing to do" >&2
      echo "    partial work on $(uname -s)." >&2
      exit 1
    fi
    ;;
esac

DEST="$RESOURCES_ROOT/$TARGET"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

echo "[*] stage-runtime: target=$TARGET lock=$LOCKFILE dest=$DEST"

# Download $1=url $2=expected-sha256 $3=expected-size-bytes -> prints path in $WORK.
# Fails closed: a mismatch deletes the partial file and exits non-zero.
_fetch_verified() {
  local url="$1" want_sha="$2" want_size="$3" out
  out="$WORK/$(basename "$1")"
  echo "    downloading $(basename "$url") ($((want_size / 1024 / 1024)) MiB)..." >&2
  curl -fsSL --retry 3 --retry-delay 2 -o "$out" "$url"
  local got_size got_sha
  got_size="$(stat -c '%s' "$out" 2>/dev/null || stat -f '%z' "$out")"
  if [ "$got_size" != "$want_size" ]; then
    rm -f "$out"
    echo "[x] size mismatch for $url: got $got_size, pinned $want_size" >&2
    exit 1
  fi
  got_sha="$(SHA256 "$out")"
  if [ "$got_sha" != "$want_sha" ]; then
    rm -f "$out"
    echo "[x] sha256 mismatch for $url: got $got_sha, pinned $want_sha" >&2
    exit 1
  fi
  echo "    verified sha256 $got_sha" >&2
  printf '%s' "$out"
}

# ---- already staged with matching hashes? skip the network entirely -----------
_already_staged() {
  jq -e '.targets[$t].entries // empty' --arg t "$TARGET" "$LOCKFILE" >/dev/null 2>&1 || return 1
  local n
  n="$(jq -r '.targets[$t].entries | length' --arg t "$TARGET" "$LOCKFILE")"
  [ "$n" -gt 0 ] || return 1
  local i path want_sha got_sha
  for ((i = 0; i < n; i++)); do
    path="$(jq -r ".targets[\$t].entries[$i].path" --arg t "$TARGET" "$LOCKFILE")"
    want_sha="$(jq -r ".targets[\$t].entries[$i].sha256" --arg t "$TARGET" "$LOCKFILE")"
    [ -f "$DEST/$path" ] || return 1
    got_sha="$(SHA256 "$DEST/$path")"
    [ "$got_sha" = "$want_sha" ] || return 1
  done
  return 0
}

if _already_staged; then
  echo "[ok] $TARGET already staged under $DEST with matching sha256 for every entry — skipping download."
  exit 0
fi
rm -rf "$DEST"
mkdir -p "$DEST"

# ---- x86_64/aarch64-unknown-linux-gnu: static tarball, extract a curated subset --
_stage_linux() {
  local url want_sha want_size archive n i
  url="$(jq -r '.targets[$t].download.url' --arg t "$TARGET" "$LOCKFILE")"
  want_sha="$(jq -r '.targets[$t].download.sha256' --arg t "$TARGET" "$LOCKFILE")"
  want_size="$(jq -r '.targets[$t].download.size_bytes' --arg t "$TARGET" "$LOCKFILE")"
  archive="$(_fetch_verified "$url" "$want_sha" "$want_size")"

  # The tarball's own top-level dir varies only by arch name; discover it instead
  # of hardcoding "podman-linux-<arch>/" so a future archive layout tweak upstream
  # doesn't silently no-op every -C extraction below.
  # (subshell + pipefail off: `head -1` closing its input early after the first
  # line otherwise SIGPIPEs `tar tzf` and, under `set -o pipefail`, aborts the
  # whole script via `set -e` even though this line did exactly what it should.)
  local top
  top="$(set +o pipefail; tar tzf "$archive" | head -1)"

  n="$(jq -r '.targets[$t].entries | length' --arg t "$TARGET" "$LOCKFILE")"
  for ((i = 0; i < n; i++)); do
    local path want_bin_sha want_bin_size mode member
    path="$(jq -r ".targets[\$t].entries[$i].path" --arg t "$TARGET" "$LOCKFILE")"
    want_bin_sha="$(jq -r ".targets[\$t].entries[$i].sha256" --arg t "$TARGET" "$LOCKFILE")"
    want_bin_size="$(jq -r ".targets[\$t].entries[$i].size_bytes" --arg t "$TARGET" "$LOCKFILE")"
    mode="$(jq -r ".targets[\$t].entries[$i].mode" --arg t "$TARGET" "$LOCKFILE")"
    case "$path" in
      libexec/podman/*) member="${top}usr/local/lib/podman/${path#libexec/podman/}" ;;
      bin/*)            member="${top}usr/local/bin/${path#bin/}" ;;
      etc/*)             member="${top}${path}" ;;
      *) echo "[x] unexpected entry path in lock file: $path" >&2; exit 1 ;;
    esac
    mkdir -p "$WORK/x" "$(dirname "$DEST/$path")"
    tar xzf "$archive" -C "$WORK/x" "$member"
    mv "$WORK/x/$member" "$DEST/$path"
    rm -rf "${WORK:?}/x"
    chmod "$mode" "$DEST/$path"
    local got_bin_sha got_bin_size
    got_bin_sha="$(SHA256 "$DEST/$path")"
    got_bin_size="$(stat -c '%s' "$DEST/$path" 2>/dev/null || stat -f '%z' "$DEST/$path")"
    if [ "$got_bin_sha" != "$want_bin_sha" ] || [ "$got_bin_size" != "$want_bin_size" ]; then
      rm -f "$DEST/$path"
      echo "[x] staged $path does not match runtime-manifest.lock (sha256 or size) — refusing to keep it." >&2
      exit 1
    fi
    echo "    staged $path ($got_bin_size bytes, sha256 verified)"
  done

  # podman's rootless network backend looks for a binary literally named
  # 'pasta' (containers/common default); upstream ships it as passt+symlink.
  ln -sf passt "$DEST/bin/pasta"
  echo "    linked bin/pasta -> bin/passt"
}

# ---- aarch64-apple-darwin: expand the official .pkg (never install it) + VM ---
_stage_macos() {
  local url want_sha want_size pkg expanded
  url="$(jq -r '.targets[$t].download.url' --arg t "$TARGET" "$LOCKFILE")"
  want_sha="$(jq -r '.targets[$t].download.sha256' --arg t "$TARGET" "$LOCKFILE")"
  want_size="$(jq -r '.targets[$t].download.size_bytes' --arg t "$TARGET" "$LOCKFILE")"
  pkg="$(_fetch_verified "$url" "$want_sha" "$want_size")"

  expanded="$WORK/pkg-expanded"
  rm -rf "$expanded"
  pkgutil --expand-full "$pkg" "$expanded"

  mkdir -p "$DEST/bin"
  local found
  for bin in podman gvproxy vfkit; do
    found="$(set +o pipefail; find "$expanded" -type f -name "$bin" -perm -u+x | head -1)"
    [ -n "$found" ] || { echo "[x] '$bin' not found inside the expanded .pkg payload" >&2; exit 1; }
    cp -p "$found" "$DEST/bin/$bin"
    chmod 0755 "$DEST/bin/$bin"
    echo "    staged bin/$bin ($(SHA256 "$DEST/bin/$bin"))"
  done

  # krunkit: alternative (libkrun/GPU) machine provider, bundled so the app can
  # start a pre-existing libkrun machine it adopts (see T024 quickstart) without
  # requiring the owner to already have krunkit installed.
  local kurl ksha ksize karchive kroot
  kurl="$(jq -r '.targets[$t].krunkit.download.url' --arg t "$TARGET" "$LOCKFILE")"
  ksha="$(jq -r '.targets[$t].krunkit.download.sha256' --arg t "$TARGET" "$LOCKFILE")"
  ksize="$(jq -r '.targets[$t].krunkit.download.size_bytes' --arg t "$TARGET" "$LOCKFILE")"
  karchive="$(_fetch_verified "$kurl" "$ksha" "$ksize")"
  kroot="$WORK/krunkit"
  mkdir -p "$kroot"
  tar xzf "$karchive" -C "$kroot"
  cp -p "$kroot/bin/krunkit" "$DEST/bin/krunkit"
  chmod 0755 "$DEST/bin/krunkit"
  mkdir -p "$DEST/lib" "$DEST/share/krunkit"
  cp -p "$kroot"/lib/*.dylib "$DEST/lib/"
  cp -p "$kroot/share/krunkit/KRUN_EFI.silent.fd" "$DEST/share/krunkit/"
  echo "    staged bin/krunkit + lib/*.dylib + share/krunkit/KRUN_EFI.silent.fd"

  # Machine image: pulled by BLOB DIGEST from the registry's content-addressable
  # blob store — the digest below IS the sha256 of the blob by OCI protocol
  # invariant, so this is a real verified-download, not a self-check.
  local oci_ref blob_digest blob_size blob_name
  oci_ref="$(jq -r '.targets[$t].machine_image.oci_ref' --arg t "$TARGET" "$LOCKFILE")"
  blob_digest="$(jq -r '.targets[$t].machine_image.blob_digest' --arg t "$TARGET" "$LOCKFILE")"
  blob_size="$(jq -r '.targets[$t].machine_image.size_bytes' --arg t "$TARGET" "$LOCKFILE")"
  blob_name="$(jq -r '.targets[$t].machine_image.blob_filename' --arg t "$TARGET" "$LOCKFILE")"
  local registry_host registry_repo
  registry_host="${oci_ref%%/*}"
  registry_repo="${oci_ref#*/}"; registry_repo="${registry_repo%%:*}"
  mkdir -p "$DEST/machine"
  echo "    downloading machine image $blob_name ($((blob_size / 1024 / 1024)) MiB)..."
  curl -fSL --retry 3 --retry-delay 2 \
    -o "$DEST/machine/$blob_name" \
    "https://$registry_host/v2/$registry_repo/blobs/$blob_digest"
  local got_sha
  got_sha="sha256:$(SHA256 "$DEST/machine/$blob_name")"
  if [ "$got_sha" != "$blob_digest" ]; then
    rm -f "$DEST/machine/$blob_name"
    echo "[x] machine image digest mismatch: got $got_sha, pinned $blob_digest" >&2
    exit 1
  fi
  echo "    verified machine image digest $got_sha"
}

case "$TARGET" in
  x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu) _stage_linux ;;
  aarch64-apple-darwin)                               _stage_macos ;;
esac

total_bytes="$(find "$DEST" -type f -exec stat -c '%s' {} \; 2>/dev/null | awk '{s+=$1} END {print s+0}')"
[ -n "$total_bytes" ] && [ "$total_bytes" -gt 0 ] || \
  total_bytes="$(find "$DEST" -type f -exec stat -f '%z' {} \; | awk '{s+=$1} END {print s+0}')"
cap_bytes="$(jq -r '.github_release_asset_cap_bytes' "$LOCKFILE")"
echo "[ok] $TARGET staged: $((total_bytes / 1024 / 1024)) MiB under $DEST"
echo "     (GitHub Releases per-file cap: $((cap_bytes / 1024 / 1024 / 1024)) GiB — the runtime bundle alone is $(awk -v b="$total_bytes" -v c="$cap_bytes" 'BEGIN{printf "%.1f", 100*b/c}')% of it; the rest of the budget is the Tauri shell + the app itself)"
