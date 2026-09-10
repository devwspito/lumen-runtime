"""runtime_manifest — fetch + verify the signed `runtime-manifest.json`
(contracts/update.md §2-3, T005).

This is what turns "there might be a new version" into a fact the daemon can
act on: the engine/companion digests currently published, signed so a
compromised or spoofed CDN response can never make the update footer light
up. FAIL-CLOSED by construction (Constitution Principle IV — "manifiesto sin
firma valida -> sin boton"): any failure (network, malformed JSON, missing
or invalid signature, no public key configured) returns None, and the
caller MUST treat None as "nothing verified", never as "nothing new".

Signing key: Ed25519, the SAME primitive and hex encoding
`hermes.config_sync.signature.verify_bundle` already verifies for cloud
policy bundles — reused here rather than introducing a second crypto
format. This is a DELIBERATE, DOCUMENTED deviation from contracts/update.md
where it says runtime-manifest.json is signed "con la misma clave [minisign]"
as latest.json: minisign is a hard external requirement of the Tauri
updater plugin for latest.json (T014/T023, out of this module's scope), but
runtime-manifest.json is a Safent-only format with no such constraint, so it
reuses the primitive this codebase already has instead of adding a second
one. See contracts/update.md's own note for the full rationale. The private
key lives ONLY in release tooling (ops/container/sign_runtime_manifest.py)
run by the owner / the publish pipeline (T023) — this module, like every
daemon-side consumer, holds only the PUBLIC key.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import urllib.request
from dataclasses import dataclass, field

from hermes.config_sync.signature import verify_bundle

logger = logging.getLogger("hermes.shell_server.runtime_manifest")

_MANIFEST_URL = os.environ.get(
    "SAFENT_RUNTIME_MANIFEST_URL",
    "https://raw.githubusercontent.com/devwspito/safent-runtime/main/runtime-manifest.json",
)
# No baked-in default. The keypair is generated once by the owner (see
# ops/container/sign_runtime_manifest.py keygen) and the PUBLIC half is
# deployed via this env var — absent config means every manifest is
# unverifiable, which is the correct fail-closed default, not a bug.
_PUBKEY_HEX = os.environ.get("SAFENT_RUNTIME_MANIFEST_PUBKEY", "")

SIGNATURE_FIELD = "signature_hex"


@dataclass(frozen=True)
class RuntimeManifest:
    """Verified payload of runtime-manifest.json (contracts/update.md §2)."""

    version: str
    engine: dict[str, str] = field(default_factory=dict)
    companion: dict[str, dict[str, str]] = field(default_factory=dict)
    min_app_version: str = ""


def current_arch_key() -> str:
    """The `os/arch` key this manifest indexes digests by, for THIS host."""
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"linux/{arch}"


def canonical_bytes(payload: dict[str, object]) -> bytes:
    """Deterministic encoding signed/verified — same rules as
    `hermes.config_sync.policy_document.canonical_bytes`: sorted keys, no
    extra whitespace, ASCII-only, so both sides byte-identically agree."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return raw.encode("ascii")


def _fetch_raw() -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(_MANIFEST_URL, timeout=5) as r:  # noqa: S310 - fixed, non-user-controlled URL
            parsed: object = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - network/parse is best-effort, caller fails closed
        logger.warning("hermes.runtime_manifest.fetch_failed")
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_str_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _as_nested_str_dict(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    return {str(k): _as_str_dict(v) for k, v in value.items()}


def _verify_and_parse(raw: dict[str, object]) -> RuntimeManifest | None:
    signature_hex = raw.get(SIGNATURE_FIELD)
    if not isinstance(signature_hex, str):
        logger.warning("hermes.runtime_manifest.missing_signature")
        return None
    payload = {k: v for k, v in raw.items() if k != SIGNATURE_FIELD}
    verified = verify_bundle(
        payload_canonical=canonical_bytes(payload),
        signature_hex=signature_hex,
        pubkey_hex=_PUBKEY_HEX,
    )
    if not verified:
        logger.warning("hermes.runtime_manifest.signature_invalid")
        return None
    try:
        return RuntimeManifest(
            version=str(payload["version"]),
            engine=_as_str_dict(payload.get("engine")),
            companion=_as_nested_str_dict(payload.get("companion")),
            min_app_version=str(payload.get("min_app_version") or payload["version"]),
        )
    except (KeyError, TypeError) as exc:
        logger.warning("hermes.runtime_manifest.malformed: %s", type(exc).__name__)
        return None


def fetch_verified_manifest() -> RuntimeManifest | None:
    """Fetch + verify runtime-manifest.json. None on ANY failure (fail-closed)."""
    if not _PUBKEY_HEX:
        logger.warning("hermes.runtime_manifest.pubkey_not_configured")
        return None
    raw = _fetch_raw()
    if not isinstance(raw, dict):
        return None
    return _verify_and_parse(raw)


def pieces_for_arch(manifest: RuntimeManifest, arch_key: str) -> list[dict[str, str]]:
    """The published pieces relevant to THIS host's architecture — informational
    for the UI/wrapper (the wrapper is the one that knows what is actually
    running and therefore what is genuinely newer, via `safent facts --json`)."""
    pieces: list[dict[str, str]] = []
    engine_digest = manifest.engine.get(arch_key)
    if engine_digest:
        pieces.append({"kind": "engine", "digest": engine_digest})
    for slug, per_arch in manifest.companion.items():
        digest = per_arch.get(arch_key)
        if digest:
            pieces.append({"kind": "companion", "slug": slug, "digest": digest})
    return pieces
