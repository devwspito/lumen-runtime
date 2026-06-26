"""Tests NodeEnrollmentService (FR-007)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hermes.agents_os.application.node_enrollment import (
    EnrollmentChallenge,
    EnrollmentChallengeMismatch,
    EnrollmentState,
    EnrollmentStateInvalid,
    NodeEnrollmentService,
    OperationalModel,
    build_challenge,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def service() -> NodeEnrollmentService:
    return NodeEnrollmentService()


@pytest.fixture
def secret() -> bytes:
    return secrets.token_bytes(32)


@pytest.fixture
def tenant_id():
    return uuid4()


class TestRequest:
    def test_request_returns_requested(
        self, service: NodeEnrollmentService
    ) -> None:
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-1",
        )
        assert snap.state == EnrollmentState.REQUESTED
        assert snap.enrolled_at is None


class TestChallenge:
    def test_valid_challenge_accepted(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
        tenant_id,
    ) -> None:
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-1",
        )
        ch = build_challenge(
            tenant_id=tenant_id,
            instance_id=snap.node_installation_id,
            shared_secret=secret,
        )
        snap = service.receive_challenge(
            enrollment_id=snap.enrollment_id,
            challenge=ch,
            shared_secret=secret,
        )
        assert snap.state == EnrollmentState.CHALLENGE_RECEIVED
        assert snap.last_challenge is not None
        assert snap.tenant_id == tenant_id

    def test_tampered_challenge_rejected(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
        tenant_id,
    ) -> None:
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-1",
        )
        ch = build_challenge(
            tenant_id=tenant_id,
            instance_id=snap.node_installation_id,
            shared_secret=secret,
        )
        # Tampering: cambiamos la signature.
        bad = EnrollmentChallenge(
            nonce_hex=ch.nonce_hex,
            tenant_id=tenant_id,
            challenge_signature_hex="ff" * 32,
            issued_at=ch.issued_at,
            expires_at=ch.expires_at,
        )
        with pytest.raises(EnrollmentChallengeMismatch):
            service.receive_challenge(
                enrollment_id=snap.enrollment_id,
                challenge=bad,
                shared_secret=secret,
            )

    def test_wrong_shared_secret_rejected(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
        tenant_id,
    ) -> None:
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-1",
        )
        ch = build_challenge(
            tenant_id=tenant_id,
            instance_id=snap.node_installation_id,
            shared_secret=secret,
        )
        other_secret = secrets.token_bytes(32)
        with pytest.raises(EnrollmentChallengeMismatch):
            service.receive_challenge(
                enrollment_id=snap.enrollment_id,
                challenge=ch,
                shared_secret=other_secret,
            )

    def test_wrong_instance_id_in_challenge_rejected(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
        tenant_id,
    ) -> None:
        """Challenge signed for instance A must not verify for instance B (P3 binding)."""
        snap_a = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-a",
        )
        snap_b = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-b",
        )
        # Challenge signed for instance A.
        ch_for_a = build_challenge(
            tenant_id=tenant_id,
            instance_id=snap_a.node_installation_id,
            shared_secret=secret,
        )
        # Presenting it to instance B must fail.
        with pytest.raises(EnrollmentChallengeMismatch):
            service.receive_challenge(
                enrollment_id=snap_b.enrollment_id,
                challenge=ch_for_a,
                shared_secret=secret,
            )

    def test_wrong_tenant_id_in_challenge_rejected(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
    ) -> None:
        """Challenge signed for tenant A must not verify for tenant B (P3 binding)."""
        tenant_a = uuid4()
        tenant_b = uuid4()
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-x",
        )
        # Challenge signed for tenant_a.
        ch_for_a = build_challenge(
            tenant_id=tenant_a,
            instance_id=snap.node_installation_id,
            shared_secret=secret,
        )
        # Alter the tenant_id on the challenge object to tenant_b (shuffle attack).
        shuffled = EnrollmentChallenge(
            nonce_hex=ch_for_a.nonce_hex,
            tenant_id=tenant_b,
            challenge_signature_hex=ch_for_a.challenge_signature_hex,
            issued_at=ch_for_a.issued_at,
            expires_at=ch_for_a.expires_at,
        )
        with pytest.raises(EnrollmentChallengeMismatch):
            service.receive_challenge(
                enrollment_id=snap.enrollment_id,
                challenge=shuffled,
                shared_secret=secret,
            )


class TestSolve:
    def test_solve_returns_proof(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
        tenant_id,
    ) -> None:
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-aaaa",
        )
        ch = build_challenge(
            tenant_id=tenant_id,
            instance_id=snap.node_installation_id,
            shared_secret=secret,
        )
        service.receive_challenge(
            enrollment_id=snap.enrollment_id,
            challenge=ch,
            shared_secret=secret,
        )
        solved, proof = service.solve_challenge(
            enrollment_id=snap.enrollment_id, shared_secret=secret
        )
        assert solved.state == EnrollmentState.CHALLENGE_SOLVED
        assert len(proof) == 64  # SHA-256 hex

    def test_proof_bound_to_tenant(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
    ) -> None:
        """Proof produced for tenant A must not verify against tenant B HMAC (P3 binding).

        The cloud computes expected_proof with its tenant_id; if the proof were
        issued for a different tenant, constant_time_eq would fail.
        """
        import hashlib
        import hmac as _hmac

        tenant_a = uuid4()
        tenant_b = uuid4()
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.CLOUD_SAAS_MANAGED,
            control_plane_endpoint="https://cp.example.com",
            hardware_fingerprint="fp-proof-tenant",
        )
        ch = build_challenge(
            tenant_id=tenant_a,
            instance_id=snap.node_installation_id,
            shared_secret=secret,
        )
        service.receive_challenge(
            enrollment_id=snap.enrollment_id,
            challenge=ch,
            shared_secret=secret,
        )
        _, proof_hex = service.solve_challenge(
            enrollment_id=snap.enrollment_id, shared_secret=secret
        )
        nonce = bytes.fromhex(ch.nonce_hex)
        fp = snap.hardware_fingerprint

        # Recompute what the cloud would expect for tenant_a — must match.
        expected_a = _hmac.new(
            secret,
            nonce + b"|" + fp.encode() + b"|" + str(tenant_a).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert _hmac.compare_digest(proof_hex, expected_a)

        # Compute what the cloud would expect for tenant_b — must NOT match.
        expected_b = _hmac.new(
            secret,
            nonce + b"|" + fp.encode() + b"|" + str(tenant_b).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert not _hmac.compare_digest(proof_hex, expected_b)


class TestComplete:
    def test_complete_enrollment_transitions_to_enrolled(
        self,
        service: NodeEnrollmentService,
        secret: bytes,
        tenant_id,
    ) -> None:
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.SELF_HOSTED,
            control_plane_endpoint="https://localhost:8443",
            hardware_fingerprint="fp-self",
        )
        ch = build_challenge(
            tenant_id=tenant_id,
            instance_id=snap.node_installation_id,
            shared_secret=secret,
        )
        service.receive_challenge(
            enrollment_id=snap.enrollment_id,
            challenge=ch,
            shared_secret=secret,
        )
        service.solve_challenge(
            enrollment_id=snap.enrollment_id, shared_secret=secret
        )
        enrolled = service.complete_enrollment(
            enrollment_id=snap.enrollment_id,
            issued_node_cert_hex="ab" * 32,
        )
        assert enrolled.state == EnrollmentState.ENROLLED
        assert enrolled.enrolled_at is not None
        assert enrolled.issued_node_cert_hex == "ab" * 32

    def test_complete_without_solve_blocked(
        self, service: NodeEnrollmentService
    ) -> None:
        snap = service.request_enrollment(
            node_installation_id=uuid4(),
            operational_model=OperationalModel.SELF_HOSTED,
            control_plane_endpoint="https://localhost:8443",
            hardware_fingerprint="fp-self",
        )
        with pytest.raises(EnrollmentStateInvalid):
            service.complete_enrollment(
                enrollment_id=snap.enrollment_id,
                issued_node_cert_hex="00" * 32,
            )
