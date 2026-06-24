"""Regression: the owner's TOTP must reach the approval gate (bug A1, 2026-06-24).

The bug: the web/D-Bus approve path forwarded NO mfa_factors to the gate, so the gate
re-verified with ``mfa_factors=None`` → always failed → the error crossed D-Bus as a
generic fault and surfaced to the owner as "el agente no está disponible". A correct TOTP
could never approve anything.

The fix threads the owner's TOTP to ``gate.approve(mfa_factors=...)``. TOTP-only model:
a valid TOTP mints a single-use token; an invalid or absent factor fails CLOSED (the gate
never mints a token without the owner's fresh code). The TOTP secret is owner-only (0600),
out of the caged agent's reach, so the agent cannot self-approve.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from hermes.agents_os.application.audit_hash_chain import AuditHashChainSigner
from hermes.agents_os.infrastructure.sqlite_audit_repository import SqliteAuditRepository
from hermes.capabilities.application.hitl_approval_minter import HitlApprovalMinter
from hermes.capabilities.domain.ports import ConsentContext, RiskLevel
from hermes.capabilities.infrastructure.sqlite_approval_gate import (
    ApprovalGateError,
    SqliteApprovalGate,
)
from hermes.capabilities.testing.fake_external_anchor import FakeExternalAnchor
from hermes.shell_server.security.mfa import MfaStore, totp_now
from hermes.shell_server.security.mfa_tool_tier import MfaFactors, MfaToolTierVerifier

_SIGNING_KEY = os.urandom(32)
_TENANT_ID = uuid4()
_OPERATOR_ID = uuid4()
_APPROVED_BY = uuid4()


def _make_gate(tmp_path) -> tuple[SqliteApprovalGate, str]:
    """A real gate with an enrolled owner MFA verifier. Returns (gate, totp_secret)."""
    audit_repo = SqliteAuditRepository(
        db_path=tmp_path / "audit.db", external_anchor=FakeExternalAnchor()
    )
    store = MfaStore(store_dir=tmp_path / "mfa")
    _, secret = store.enroll()
    gate = SqliteApprovalGate(
        db_path=tmp_path / "approvals.db",
        minter=HitlApprovalMinter(signing_key=_SIGNING_KEY),
        signer=AuditHashChainSigner(signing_key=_SIGNING_KEY),
        audit_repo=audit_repo,
        mfa_verifier=MfaToolTierVerifier(store),
    )
    return gate, secret


async def _register(gate: SqliteApprovalGate, proposal_id) -> None:
    await gate.register_pending(
        proposal_id=proposal_id,
        work_item_id=uuid4(),
        consent_context=ConsentContext(tenant_id=_TENANT_ID, operator_id=_OPERATOR_ID),
        risk=RiskLevel.HIGH,
        justification="A1 regression",
        parameters_redacted={"path": "/tmp/out.txt"},
    )


@pytest.mark.asyncio
async def test_valid_totp_reaches_gate_and_mints_token(tmp_path) -> None:
    """A correct owner TOTP forwarded to the gate mints an approval token (A1 fixed)."""
    gate, secret = _make_gate(tmp_path)
    pid = uuid4()
    await _register(gate, pid)
    token = await gate.approve(
        proposal_id=pid,
        approved_by=_APPROVED_BY,
        mfa_factors=MfaFactors(totp=totp_now(secret)),
    )
    assert token, "a valid owner TOTP must mint an approval token"


@pytest.mark.asyncio
async def test_wrong_totp_fails_closed(tmp_path) -> None:
    gate, _ = _make_gate(tmp_path)
    pid = uuid4()
    await _register(gate, pid)
    with pytest.raises(ApprovalGateError):
        await gate.approve(
            proposal_id=pid,
            approved_by=_APPROVED_BY,
            mfa_factors=MfaFactors(totp="000000"),
        )


@pytest.mark.asyncio
async def test_absent_factors_fail_closed(tmp_path) -> None:
    """The pre-fix behaviour (no factors) must REJECT, never silently mint."""
    gate, _ = _make_gate(tmp_path)
    pid = uuid4()
    await _register(gate, pid)
    with pytest.raises(ApprovalGateError):
        await gate.approve(proposal_id=pid, approved_by=_APPROVED_BY, mfa_factors=None)
