"""Ed25519 bundle signature verification.

Security invariants:
- FAIL-CLOSED: any exception (bad key, bad sig, bad hex, wrong payload) → False.
  The caller must treat False as "do not apply" — no exceptions propagate upward.
- The associate stores only the PUBLIC key (signing_pubkey_hex from the pairing).
  The cloud holds the private key (KMS-backed, Fase 7).
- Constant-time comparison is handled by cryptography's Ed25519 implementation.
"""

from __future__ import annotations

import logging

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger("hermes.config_sync.signature")


def verify_bundle(
    *,
    payload_canonical: bytes,
    signature_hex: str,
    pubkey_hex: str,
) -> bool:
    """Verify an Ed25519 bundle signature.

    Returns True only if the signature is valid for the given payload and key.
    Returns False on ANY failure — bad hex, wrong key, tampered payload, etc.

    The caller is responsible for passing `canonical_bytes(payload)` as
    `payload_canonical`; this function has no knowledge of the bundle structure.
    """
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        signature = bytes.fromhex(signature_hex)
        public_key.verify(signature, payload_canonical)
        return True
    except Exception as exc:  # noqa: BLE001 — intentionally broad; fail-closed
        logger.warning(
            "hermes.config_sync.signature_invalid",
            extra={"reason": type(exc).__name__, "detail": str(exc)[:120]},
        )
        return False
