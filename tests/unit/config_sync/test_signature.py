"""Tests for Ed25519 bundle signature verification.

P0-1: All signatures are over the full envelope produced by signing_bytes()
(version + tenant_id + issued_at + payload), not just the payload bytes.

Uses a freshly generated key pair per test — no hardcoded key material.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hermes.config_sync.policy_document import PolicyPayload, signing_bytes
from hermes.config_sync.signature import verify_bundle

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Return (private_key, pubkey_hex)."""
    private_key = Ed25519PrivateKey.generate()
    pubkey_bytes = private_key.public_key().public_bytes_raw()
    return private_key, pubkey_bytes.hex()


def _minimal_payload() -> PolicyPayload:
    return PolicyPayload.model_validate(
        {
            "agents": [],
            "providers": [],
            "integrations": [],
            "mcp": [],
            "skills": [],
            "egress": {"allow_domains": []},
            "consents": [],
            "features": {"views": []},
            "license": {"plan": "starter", "max_agents": 5, "expires_at": "", "views": []},
        }
    )


def _sign_envelope(
    private_key: Ed25519PrivateKey,
    payload: PolicyPayload,
    *,
    version: int = 1,
    tenant_id: str = "tenant-1",
    issued_at: str = "2026-06-26T10:00:00Z",
) -> tuple[bytes, str]:
    """Sign the full envelope; return (envelope_bytes, signature_hex)."""
    envelope = signing_bytes(
        version=version,
        tenant_id=tenant_id,
        issued_at=issued_at,
        payload=payload,
    )
    sig_hex = private_key.sign(envelope).hex()
    return envelope, sig_hex


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestVerifyBundleHappyPath:
    def test_valid_signature_returns_true(self) -> None:
        private_key, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope, sig_hex = _sign_envelope(private_key, payload)

        result = verify_bundle(
            payload_canonical=envelope,
            signature_hex=sig_hex,
            pubkey_hex=pubkey_hex,
        )
        assert result is True

    def test_payload_with_agents_verifies(self) -> None:
        from hermes.config_sync.policy_document import AgentSpec

        private_key, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        payload.agents.append(
            AgentSpec(agent_id="a1", name="Support", provider_alias="gpt4")
        )
        envelope, sig_hex = _sign_envelope(private_key, payload)

        assert verify_bundle(
            payload_canonical=envelope,
            signature_hex=sig_hex,
            pubkey_hex=pubkey_hex,
        )

    def test_different_versions_each_verify_with_their_own_envelope(self) -> None:
        """Two bundles with different versions both verify with their own envelopes."""
        private_key, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()

        envelope_v1, sig_v1 = _sign_envelope(private_key, payload, version=1)
        envelope_v2, sig_v2 = _sign_envelope(private_key, payload, version=2)

        assert verify_bundle(payload_canonical=envelope_v1, signature_hex=sig_v1, pubkey_hex=pubkey_hex)
        assert verify_bundle(payload_canonical=envelope_v2, signature_hex=sig_v2, pubkey_hex=pubkey_hex)


# ---------------------------------------------------------------------------
# P0-1: Mutating envelope fields invalidates the signature
# ---------------------------------------------------------------------------


class TestEnvelopeFieldMutationInvalidatesSignature:
    def test_mutating_version_invalidates_signature(self) -> None:
        """An attacker cannot replay a bundle under a different version number."""
        private_key, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope_v1, sig_v1 = _sign_envelope(private_key, payload, version=1)
        # Build envelope for version=2 but present the version=1 signature.
        envelope_v2 = signing_bytes(
            version=2, tenant_id="tenant-1", issued_at="2026-06-26T10:00:00Z", payload=payload
        )
        assert not verify_bundle(
            payload_canonical=envelope_v2,
            signature_hex=sig_v1,
            pubkey_hex=pubkey_hex,
        )

    def test_mutating_tenant_id_invalidates_signature(self) -> None:
        """An attacker cannot replay a bundle across tenants."""
        private_key, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope_a, sig_a = _sign_envelope(private_key, payload, tenant_id="tenant-a")
        # Present the tenant-a signature with a tenant-b envelope.
        envelope_b = signing_bytes(
            version=1, tenant_id="tenant-b", issued_at="2026-06-26T10:00:00Z", payload=payload
        )
        assert not verify_bundle(
            payload_canonical=envelope_b,
            signature_hex=sig_a,
            pubkey_hex=pubkey_hex,
        )

    def test_mutating_issued_at_invalidates_signature(self) -> None:
        """Changing the timestamp invalidates the signature (replay with old timestamp blocked)."""
        private_key, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope_now, sig_now = _sign_envelope(private_key, payload, issued_at="2026-06-26T10:00:00Z")
        # Present the current signature with a stale timestamp envelope.
        envelope_stale = signing_bytes(
            version=1, tenant_id="tenant-1", issued_at="2025-01-01T00:00:00Z", payload=payload
        )
        assert not verify_bundle(
            payload_canonical=envelope_stale,
            signature_hex=sig_now,
            pubkey_hex=pubkey_hex,
        )


# ---------------------------------------------------------------------------
# Tamper / wrong key / bad input — all must return False without raising
# ---------------------------------------------------------------------------


class TestVerifyBundleFailClosed:
    def test_tampered_envelope_returns_false(self) -> None:
        private_key, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope, sig_hex = _sign_envelope(private_key, payload)

        tampered = bytearray(envelope)
        tampered[0] ^= 0xFF
        assert not verify_bundle(
            payload_canonical=bytes(tampered),
            signature_hex=sig_hex,
            pubkey_hex=pubkey_hex,
        )

    def test_wrong_pubkey_returns_false(self) -> None:
        private_key, _good_pubkey = _generate_keypair()
        _, wrong_pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope, sig_hex = _sign_envelope(private_key, payload)

        assert not verify_bundle(
            payload_canonical=envelope,
            signature_hex=sig_hex,
            pubkey_hex=wrong_pubkey_hex,
        )

    def test_garbage_signature_hex_returns_false(self) -> None:
        _, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope, _ = _sign_envelope(Ed25519PrivateKey.generate(), payload)

        assert not verify_bundle(
            payload_canonical=envelope,
            signature_hex="not_valid_hex_at_all",
            pubkey_hex=pubkey_hex,
        )

    def test_garbage_pubkey_hex_returns_false(self) -> None:
        private_key, _ = _generate_keypair()
        payload = _minimal_payload()
        envelope, sig_hex = _sign_envelope(private_key, payload)

        assert not verify_bundle(
            payload_canonical=envelope,
            signature_hex=sig_hex,
            pubkey_hex="gggg",  # invalid hex
        )

    def test_empty_signature_returns_false(self) -> None:
        _, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope, _ = _sign_envelope(Ed25519PrivateKey.generate(), payload)

        assert not verify_bundle(
            payload_canonical=envelope,
            signature_hex="",
            pubkey_hex=pubkey_hex,
        )

    def test_empty_pubkey_returns_false(self) -> None:
        private_key, _ = _generate_keypair()
        payload = _minimal_payload()
        envelope, sig_hex = _sign_envelope(private_key, payload)

        assert not verify_bundle(
            payload_canonical=envelope,
            signature_hex=sig_hex,
            pubkey_hex="",
        )

    def test_signature_wrong_length_returns_false(self) -> None:
        _, pubkey_hex = _generate_keypair()
        payload = _minimal_payload()
        envelope, _ = _sign_envelope(Ed25519PrivateKey.generate(), payload)

        assert not verify_bundle(
            payload_canonical=envelope,
            signature_hex="deadbeef",  # too short for Ed25519
            pubkey_hex=pubkey_hex,
        )

    def test_does_not_raise_on_any_garbage_input(self) -> None:
        """The API contract guarantees no exception propagates to the caller."""
        result = verify_bundle(
            payload_canonical=b"",
            signature_hex="x" * 50,
            pubkey_hex="y" * 20,
        )
        assert result is False
