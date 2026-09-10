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
# Exit codes (every non-zero exit in this script and in lib/fetch-verified.sh
# uses one of these — "the install never fails" means failing LOUDLY and
# distinguishably, not silently or ambiguously):
#   0  success (including "already staged, nothing to do")
#   1  usage error: bad/missing target argument, or a required tool
#      (curl/jq/tar/pkgutil) is missing from PATH
#   2  download failed: the network never delivered a complete transfer
#      despite every retry (EXIT_DOWNLOAD, see lib/fetch-verified.sh)
#   3  integrity failure: a transfer completed (right byte count) but its
#      sha256 did not match the pinned lock, twice in a row — corruption,
#      not incompleteness (EXIT_INTEGRITY, see lib/fetch-verified.sh)
#   4  staging failure: something in the lock file, an archive's layout, or
#      a .pkg's payload did not match what this script expects (not a
#      network problem — retrying would not help)
#   5  platform guard: this target must be staged on a different host OS
#      (aarch64-apple-darwin needs pkgutil, i.e. an actual macOS host/runner)
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
# Every download (archives AND the machine image) goes through
# lib/fetch-verified.sh's `fetch_verified`: resumable across both curl's own
# --retry budget and dropped connections between separate runs of this
# script (a killed/retried CI job resumes instead of restarting an 888 MiB
# transfer from zero — the exact failure a real macOS pipeline run hit
# before this existed: a connection closed 31 MB from the end and the old,
# non-resuming logic threw the whole download away).
#
# aarch64-apple-darwin must run on a macOS host/runner: it expands the official
# podman .pkg with `pkgutil --expand-full` WITHOUT installing it (no
# `installer -pkg`, no admin password) and copies the binaries out. On Linux
# this step fails fast with a clear message instead of doing partial work.
set -euo pipefail

EXIT_USAGE=1
EXIT_STAGE=4
EXIT_PLATFORM=5

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCKFILE="$DESKTOP_DIR/runtime-manifest.lock"
RESOURCES_ROOT="$DESKTOP_DIR/src-tauri/resources/runtime"
# Outside resources/runtime/ ENTIRELY, on purpose (see CACHE_DIR below): a
# real macOS notarization run (notarytool) rejected the app because raw
# download archives (krunkit-podman-unsigned-1.3.2.tgz) were being bundled
# alongside their own extracted/signed contents — Apple's tooling opens
# archives and inspects what's inside them too. bundle.resources' own glob
# is `resources/runtime/*/**/*`, which matches ANY first path segment
# including a dotdir (the `glob` crate does not exclude dot-entries from `*`
# by default) — so a cache nested ANYWHERE under resources/runtime/, even in
# a gitignored dotdir, still gets swept into the .app. Never put download
# state under resources/runtime/ again; see tests/test-packaged-layout.sh.

# shellcheck source=lib/fetch-verified.sh
source "$SCRIPT_DIR/lib/fetch-verified.sh"
# shellcheck source=lib/normalize-staged-tree.sh
source "$SCRIPT_DIR/lib/normalize-staged-tree.sh"
# shellcheck source=lib/patch-containers-conf.sh
source "$SCRIPT_DIR/lib/patch-containers-conf.sh"

TARGET="${1:-}"
case "$TARGET" in
  aarch64-apple-darwin|x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu) ;;
  x86_64-apple-darwin)
    echo "[x] x86_64-apple-darwin: not staged on purpose — v6.1.1 publishes no" >&2
    echo "    macOS Intel installer. Matches contracts/update.md (darwin-x86_64" >&2
    echo "    intentionally absent). See runtime-manifest.lock ->" >&2
    echo "    excluded_targets.x86_64-apple-darwin." >&2
    exit "$EXIT_USAGE"
    ;;
  "")
    echo "usage: $0 <aarch64-apple-darwin|x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu>" >&2
    exit "$EXIT_USAGE"
    ;;
  *)
    echo "[x] unrecognized target triple: $TARGET" >&2
    echo "    want one of: aarch64-apple-darwin | x86_64-unknown-linux-gnu | aarch64-unknown-linux-gnu" >&2
    exit "$EXIT_USAGE"
    ;;
esac

for tool in curl jq tar; do
  command -v "$tool" >/dev/null 2>&1 || { echo "[x] need '$tool' on PATH (build-time only, not shipped)" >&2; exit "$EXIT_USAGE"; }
done

[ -f "$LOCKFILE" ] || { echo "[x] missing $LOCKFILE" >&2; exit "$EXIT_USAGE"; }
case "$TARGET" in
  *-apple-darwin)
    if [ "$(uname -s)" != Darwin ]; then
      echo "[x] $TARGET must be staged on a macOS host/runner (uses pkgutil to" >&2
      echo "    expand the official .pkg WITHOUT installing it). Refusing to do" >&2
      echo "    partial work on $(uname -s)." >&2
      exit "$EXIT_PLATFORM"
    fi
    command -v pkgutil >/dev/null 2>&1 || { echo "[x] need 'pkgutil' (macOS only) on PATH" >&2; exit "$EXIT_USAGE"; }
    ;;
esac

DEST="$RESOURCES_ROOT/$TARGET"
# Persistent across runs (on purpose — a killed script resumes an in-flight
# archive/image download from here instead of restarting it): gitignored,
# but NOT under resources/runtime/ — see the comment on RESOURCES_ROOT above.
# The pipeline lane's `actions/cache` step must key/cache THIS path, not the
# old resources/runtime/.cache/ one.
CACHE_DIR="$DESKTOP_DIR/.cache/runtime-downloads/$TARGET"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT INT TERM

echo "[*] stage-runtime: target=$TARGET lock=$LOCKFILE dest=$DEST"

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
    # etc/containers/containers.conf is rewritten in place by
    # _patch_containers_conf after extraction (see there) — on disk it is
    # NEVER the pristine upstream original this entry's own hash pins, so
    # check it against containers_conf_patch's hash instead, when that key
    # exists for this target (macOS has no such entry at all).
    if [ "$path" = "etc/containers/containers.conf" ] && \
       jq -e '.targets[$t].containers_conf_patch // empty' --arg t "$TARGET" "$LOCKFILE" >/dev/null 2>&1; then
      want_sha="$(jq -r '.targets[$t].containers_conf_patch.sha256' --arg t "$TARGET" "$LOCKFILE")"
    fi
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
mkdir -p "$DEST" "$CACHE_DIR"

# ---- x86_64/aarch64-unknown-linux-gnu: static tarball, extract a curated subset --
_stage_linux() {
  local url want_sha want_size archive n i
  url="$(jq -r '.targets[$t].download.url' --arg t "$TARGET" "$LOCKFILE")"
  want_sha="$(jq -r '.targets[$t].download.sha256' --arg t "$TARGET" "$LOCKFILE")"
  want_size="$(jq -r '.targets[$t].download.size_bytes' --arg t "$TARGET" "$LOCKFILE")"
  archive="$CACHE_DIR/$(basename "$url")"
  echo "    downloading $(basename "$url") ($((want_size / 1024 / 1024)) MiB)..."
  fetch_verified "$url" "$archive" "$want_size" "$want_sha" || exit $?
  echo "    verified sha256 $want_sha"

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
      *) echo "[x] unexpected entry path in lock file: $path" >&2; exit "$EXIT_STAGE" ;;
    esac
    mkdir -p "$WORK/x" "$(dirname "$DEST/$path")"
    tar xzf "$archive" -C "$WORK/x" "$member"
    mv "$WORK/x/$member" "$DEST/$path"
    rm -rf "${WORK:?}/x"
    chmod "$mode" "$DEST/$path"
    local got_bin_sha got_bin_size
    got_bin_sha="$(SHA256 "$DEST/$path")"
    got_bin_size="$(_filesize "$DEST/$path")"
    if [ "$got_bin_sha" != "$want_bin_sha" ] || [ "$got_bin_size" != "$want_bin_size" ]; then
      rm -f "$DEST/$path"
      echo "[x] staged $path does not match runtime-manifest.lock (sha256 or size) — refusing to keep it." >&2
      exit "$EXIT_STAGE"
    fi
    echo "    staged $path ($got_bin_size bytes, sha256 verified)"
  done

  # podman's rootless network backend looks for a binary literally named
  # 'pasta' (containers/common default); upstream ships it as passt+symlink.
  ln -sf passt "$DEST/bin/pasta"
  echo "    linked bin/pasta -> bin/passt"

  _patch_containers_conf
}

# containers.conf, as extracted above (verified against its OWN entries[]
# sha256 — the pristine upstream original), does not point podman at ITS OWN
# bundled netavark/aardvark-dns/rootlessport: without this, a host that
# happens to already have podman installed silently uses THAT copy instead
# (confirmed on this DGX: podman fell back to the system's netavark 1.4.0
# instead of the bundled 2.1.0 until this patch was applied) — exactly the
# "depends on what happens to already be on the machine" failure mode the
# whole point of bundling exists to avoid. Appends, never overwrites — the
# result is verified against its OWN pinned hash (containers_conf_patch in
# the lock file), separate from the upstream original's entries[] hash,
# because this is OUR generated content, not a re-check of the same bytes.
#
# $BINDIR is podman/containers-common's OWN token (containers/common
# pkg/config/config.go, FindHelperBinary) for "the directory containing
# the CURRENTLY RUNNING podman binary" — resolved fresh at runtime via
# os.Executable(), not baked in at stage time. This is what makes the same
# staged containers.conf correct BOTH here (resources/runtime/<target>/)
# AND wherever the app later deploys the bundle for real
# (~/.safent/runtime/<version>/, per data-model.md) without stage-runtime.sh
# ever needing to know that second path.
#
# default_rootless_network_cmd = "pasta" is already podman 6.1.1's own
# default (confirmed: RELEASE_NOTES.md, "default tool for rootless
# networking has been swapped from slirp4netns to pasta") — set explicitly
# anyway so the product does not silently change behavior if a future
# podman release ever changes ITS default.
_patch_containers_conf() {
  local conf="$DEST/etc/containers/containers.conf"
  local want_sha want_size
  want_sha="$(jq -r '.targets[$t].containers_conf_patch.sha256' --arg t "$TARGET" "$LOCKFILE")"
  want_size="$(jq -r '.targets[$t].containers_conf_patch.size_bytes' --arg t "$TARGET" "$LOCKFILE")"

  patch_containers_conf_for_bundled_helpers "$conf" || exit "$EXIT_STAGE"

  local got_sha got_size
  got_sha="$(SHA256 "$conf")"
  got_size="$(_filesize "$conf")"
  if [ "$got_sha" != "$want_sha" ] || [ "$got_size" != "$want_size" ]; then
    rm -f "$conf"
    echo "[x] patched containers.conf does not match runtime-manifest.lock's containers_conf_patch (sha256 or size) — refusing to keep it." >&2
    exit "$EXIT_STAGE"
  fi
  echo "    patched etc/containers/containers.conf (helper_binaries_dir + default_rootless_network_cmd, $got_size bytes, sha256 verified)"
}

# ---- aarch64-apple-darwin: expand the official .pkg (never install it) + VM ---
_stage_macos() {
  local url want_sha want_size pkg expanded
  url="$(jq -r '.targets[$t].download.url' --arg t "$TARGET" "$LOCKFILE")"
  want_sha="$(jq -r '.targets[$t].download.sha256' --arg t "$TARGET" "$LOCKFILE")"
  want_size="$(jq -r '.targets[$t].download.size_bytes' --arg t "$TARGET" "$LOCKFILE")"
  pkg="$CACHE_DIR/$(basename "$url")"
  echo "    downloading $(basename "$url") ($((want_size / 1024 / 1024)) MiB)..."
  fetch_verified "$url" "$pkg" "$want_size" "$want_sha" || exit $?
  echo "    verified sha256 $want_sha"

  expanded="$WORK/pkg-expanded"
  rm -rf "$expanded"
  pkgutil --expand-full "$pkg" "$expanded"

  mkdir -p "$DEST/bin"
  local found
  for bin in podman gvproxy vfkit; do
    found="$(set +o pipefail; find "$expanded" -type f -name "$bin" -perm -u+x | head -1)"
    [ -n "$found" ] || { echo "[x] '$bin' not found inside the expanded .pkg payload" >&2; exit "$EXIT_STAGE"; }
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
  karchive="$CACHE_DIR/$(basename "$kurl")"
  echo "    downloading $(basename "$kurl") ($((ksize / 1024 / 1024)) MiB)..."
  fetch_verified "$kurl" "$karchive" "$ksize" "$ksha" || exit $?
  echo "    verified sha256 $ksha"
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
  # invariant, so this is a real verified-download, not a self-check. This is
  # the 888 MiB transfer that motivated fetch_verified's resume logic in the
  # first place (a real macOS run dropped 31 MB from the end of it).
  local oci_ref blob_digest blob_size blob_name
  oci_ref="$(jq -r '.targets[$t].machine_image.oci_ref' --arg t "$TARGET" "$LOCKFILE")"
  blob_digest="$(jq -r '.targets[$t].machine_image.blob_digest' --arg t "$TARGET" "$LOCKFILE")"
  blob_size="$(jq -r '.targets[$t].machine_image.size_bytes' --arg t "$TARGET" "$LOCKFILE")"
  blob_name="$(jq -r '.targets[$t].machine_image.blob_filename' --arg t "$TARGET" "$LOCKFILE")"
  local registry_host registry_repo
  registry_host="${oci_ref%%/*}"
  registry_repo="${oci_ref#*/}"; registry_repo="${registry_repo%%:*}"
  local blob_sha="${blob_digest#sha256:}"
  echo "    downloading machine image $blob_name ($((blob_size / 1024 / 1024)) MiB)..."
  fetch_verified "https://$registry_host/v2/$registry_repo/blobs/$blob_digest" \
    "$DEST/machine/$blob_name" "$blob_size" "$blob_sha" || exit $?
  echo "    verified machine image digest sha256:$blob_sha"
}

case "$TARGET" in
  x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu) _stage_linux ;;
  aarch64-apple-darwin)                               _stage_macos ;;
esac

# Permissions + symlinks + (on macOS) xattrs, uniformly, regardless of which
# staging path ran — see lib/normalize-staged-tree.sh for why this exists.
echo "    normalizing staged tree (permissions, symlinks$([ "$(uname -s)" = Darwin ] && echo ', xattrs'))..."
normalize_staged_tree "$DEST" || exit "$EXIT_STAGE"

total_bytes="$(find "$DEST" -type f -exec stat -c '%s' {} \; 2>/dev/null | awk '{s+=$1} END {print s+0}')"
[ -n "$total_bytes" ] && [ "$total_bytes" -gt 0 ] || \
  total_bytes="$(find "$DEST" -type f -exec stat -f '%z' {} \; | awk '{s+=$1} END {print s+0}')"
cap_bytes="$(jq -r '.github_release_asset_cap_bytes' "$LOCKFILE")"
echo "[ok] $TARGET staged: $((total_bytes / 1024 / 1024)) MiB under $DEST"
echo "     (GitHub Releases per-file cap: $((cap_bytes / 1024 / 1024 / 1024)) GiB — the runtime bundle alone is $(awk -v b="$total_bytes" -v c="$cap_bytes" 'BEGIN{printf "%.1f", 100*b/c}')% of it; the rest of the budget is the Tauri shell + the app itself)"
