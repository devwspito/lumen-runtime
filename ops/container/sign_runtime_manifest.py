#!/usr/bin/env python3
"""sign_runtime_manifest.py — release tooling for runtime-manifest.json
(contracts/update.md §2, T005). The daemon-side counterpart that fetches
and verifies this file is `hermes.shell_server.runtime_manifest`.

Two subcommands:
  keygen   generate a NEW Ed25519 keypair. Writes the PRIVATE key to a file
           (0600) and prints the PUBLIC key (hex) to stdout — deploy that
           value as SAFENT_RUNTIME_MANIFEST_PUBKEY wherever the daemon runs.
           Run ONCE; re-running invalidates every manifest signed with the
           previous key until every daemon's env var is rotated too.
  sign     assemble + sign runtime-manifest.json from explicit per-arch
           digests. Digests must already be resolved by the publish
           pipeline's OWN registry tooling (e.g. `docker buildx imagetools
           inspect`, T023) — this script never talks to a registry itself,
           so it has no network dependency and is fully unit-testable.

Key handling: the PRIVATE key never leaves the machine/CI job that runs
`sign` — store it as a secret (mirrors TAURI_SIGNING_PRIVATE_KEY, already
used by the desktop pipeline in agents-autonomy/.github/workflows), 0600,
never committed. The PUBLIC key is not secret: safe to bake into the daemon
image or pass as a plain env var. This is a SEPARATE keypair from the one
Tauri's updater plugin uses for latest.json/minisign (see
contracts/update.md's note on why) — do not reuse one for the other.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SIGNATURE_FIELD = "signature_hex"
_REQUIRED_DIGEST_PREFIX = "sha256:"


def canonical_bytes(payload: dict[str, object]) -> bytes:
    """MUST match hermes.shell_server.runtime_manifest.canonical_bytes
    exactly, or every signature this tool produces fails to verify."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return raw.encode("ascii")


def build_payload(
    *,
    version: str,
    engine_amd64: str,
    engine_arm64: str,
    companion_amd64: str,
    companion_arm64: str,
    podman_version: str,
    machine_os: str,
    min_app_version: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "version": version,
        "engine": {"linux/amd64": engine_amd64, "linux/arm64": engine_arm64},
        "companion": {
            "safent-ads": {"linux/amd64": companion_amd64, "linux/arm64": companion_arm64},
        },
        "runtime_bundle": {"podman": podman_version, "machine_os": machine_os},
        "min_app_version": min_app_version or version,
    }


class DigestValidationError(ValueError):
    """A digest argument is not `sha256:<hex>` — refuses to sign a mutable tag."""


def _require_digest(label: str, value: str) -> None:
    if not value.startswith(_REQUIRED_DIGEST_PREFIX):
        raise DigestValidationError(
            f"{label} must start with '{_REQUIRED_DIGEST_PREFIX}' (got {value!r}) — "
            "a tag is not acceptable here (contracts/update.md invariant #1: all digest, no tag)."
        )


def sign_payload(payload: dict[str, object], private_key: Ed25519PrivateKey) -> dict[str, object]:
    for label, value in (
        ("engine linux/amd64", payload["engine"]["linux/amd64"]),  # type: ignore[index]
        ("engine linux/arm64", payload["engine"]["linux/arm64"]),  # type: ignore[index]
        ("companion safent-ads linux/amd64", payload["companion"]["safent-ads"]["linux/amd64"]),  # type: ignore[index]
        ("companion safent-ads linux/arm64", payload["companion"]["safent-ads"]["linux/arm64"]),  # type: ignore[index]
    ):
        _require_digest(label, value)
    signature_hex = private_key.sign(canonical_bytes(payload)).hex()
    return {**payload, SIGNATURE_FIELD: signature_hex}


def _cmd_keygen(args: argparse.Namespace) -> int:
    private_key = Ed25519PrivateKey.generate()
    out_path = Path(args.out_private)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(private_key.private_bytes_raw())
    os.chmod(out_path, 0o600)
    pubkey_hex = private_key.public_key().public_bytes_raw().hex()
    print(f"[ok] private key written to {out_path} (0600) — keep secret.", file=sys.stderr)
    print(pubkey_hex)  # stdout: the ONLY thing scripts should capture
    return 0


def _cmd_sign(args: argparse.Namespace) -> int:
    payload = build_payload(
        version=args.version,
        engine_amd64=args.engine_amd64,
        engine_arm64=args.engine_arm64,
        companion_amd64=args.companion_amd64,
        companion_arm64=args.companion_arm64,
        podman_version=args.podman_version,
        machine_os=args.machine_os,
        min_app_version=args.min_app_version,
    )
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(Path(args.private_key).read_bytes())
        document = sign_payload(payload, private_key)
    except (DigestValidationError, ValueError) as exc:
        print(f"[x] {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"[ok] wrote {out_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="generate a new Ed25519 signing keypair")
    keygen.add_argument("--out-private", required=True, help="path to write the PRIVATE key (0600)")
    keygen.set_defaults(func=_cmd_keygen)

    sign = sub.add_parser("sign", help="assemble + sign runtime-manifest.json")
    sign.add_argument("--version", required=True, help="app version this manifest describes")
    sign.add_argument("--engine-amd64", required=True, help="sha256:<hex>, engine, linux/amd64")
    sign.add_argument("--engine-arm64", required=True, help="sha256:<hex>, engine, linux/arm64")
    sign.add_argument("--companion-amd64", required=True, help="sha256:<hex>, safent-ads, amd64")
    sign.add_argument("--companion-arm64", required=True, help="sha256:<hex>, safent-ads, arm64")
    sign.add_argument("--podman-version", required=True)
    sign.add_argument("--machine-os", required=True)
    sign.add_argument("--min-app-version", default=None, help="defaults to --version")
    sign.add_argument("--private-key", required=True, help="path to the private key from `keygen`")
    sign.add_argument("--out", default="runtime-manifest.json")
    sign.set_defaults(func=_cmd_sign)

    parsed = parser.parse_args(argv)
    return int(parsed.func(parsed))


if __name__ == "__main__":
    raise SystemExit(main())
