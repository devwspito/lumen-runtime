"""Regression: TOTP must be forwarded as mfa_factors to gate.approve (bug 2026-06-25).

Two root causes fixed:

  1. `approve_action` in `DbusRuntimeServiceWiring` received `totp` but NEVER passed it
     to `gate.approve()` — the `mfa_factors` argument was always None. For mfa-tier tools
     (e.g. skill_manage) the gate rejects with mfa_required, which crossed D-Bus as an
     untyped error and surfaced as "proposal_invalid". A correct TOTP was silently dropped.

  2. `_translate_dbus_error` in `DbusControlPlaneAdapter` caught ALL ApprovalGateError
     variants by string-matching the message and mapped them to reason="proposal_invalid",
     losing the real reason (mfa_required / invalid_totp / mfa_not_enrolled). The fix
     encodes the reason in the D-Bus error NAME (org.hermes.Error.ApprovalGate.<reason>)
     so the client can reconstruct it exactly.

These tests verify:
  - approve_action forwards totp → gate receives MfaFactors(totp=...) not None.
  - approve_action with no totp → gate receives mfa_factors=None (simple-tier path).
  - _translate_dbus_error extracts "mfa_required" from a structured D-Bus error name.
  - _translate_dbus_error extracts "invalid_totp" from a structured D-Bus error name.
  - _translate_dbus_error falls back to "proposal_invalid" for unstructured legacy errors.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.unit

_AUTHORIZED_UID = 1000
_SIGNING_KEY = os.urandom(32)


# ---------------------------------------------------------------------------
# Fake gate that records mfa_factors passed to approve
# ---------------------------------------------------------------------------


class _RecordingApprovalGate:
    """ApprovalGatePort fake that records mfa_factors forwarded to approve."""

    def __init__(self) -> None:
        self.approve_calls: list[dict] = []
        self.reject_calls: list[dict] = []

    async def register_pending(self, *, proposal_id, **_) -> None:
        pass

    async def approve(self, *, proposal_id: UUID, approved_by: UUID, mfa_factors=None) -> str:
        self.approve_calls.append({
            "proposal_id": proposal_id,
            "approved_by": approved_by,
            "mfa_factors": mfa_factors,
        })
        return f"fake-token-{proposal_id}"

    async def reject(self, *, proposal_id: UUID, rejected_by: UUID, reason: str) -> None:
        self.reject_calls.append({"proposal_id": proposal_id, "reason": reason})

    async def verify_token(self, *, proposal_id: UUID, token: str) -> bool:
        return True

    async def approved_token_for(self, proposal_id: UUID) -> str | None:
        return None

    async def work_item_id_for_proposal(self, proposal_id: UUID):
        return None


def _make_wiring_with_recording_gate():
    from hermes.agents_os.infrastructure.dbus_runtime_service import DbusRuntimeServiceWiring
    from hermes.tasks.testing.in_memory_agent_state import InMemoryAgentState

    state = InMemoryAgentState(paused=False)
    gate = _RecordingApprovalGate()
    wiring = DbusRuntimeServiceWiring(
        agent_state=state,
        approval_gate=gate,
        authorized_uids=frozenset({_AUTHORIZED_UID}),
    )
    return wiring, gate


# ---------------------------------------------------------------------------
# Bug #1: approve_action must forward totp as MfaFactors to gate.approve
# ---------------------------------------------------------------------------


class TestApproveActionToTPForwarding:
    """approve_action must forward totp as MfaFactors to gate.approve (bug 2026-06-25)."""

    async def test_totp_forwarded_as_mfa_factors(self) -> None:
        """When totp is provided, gate.approve receives MfaFactors(totp=...), not None."""
        wiring, gate = _make_wiring_with_recording_gate()
        proposal_id = uuid4()
        await wiring.approve_action(
            proposal_id=proposal_id,
            sender_uid=_AUTHORIZED_UID,
            totp="123456",
        )
        assert len(gate.approve_calls) == 1
        mfa = gate.approve_calls[0]["mfa_factors"]
        assert mfa is not None, (
            "gate.approve must receive MfaFactors when totp is provided — "
            "previously the totp was received by approve_action but dropped before "
            "forwarding to gate.approve, causing mfa-tier proposals to always fail."
        )
        assert mfa.totp == "123456"

    async def test_no_totp_gives_none_mfa_factors(self) -> None:
        """When totp is absent, gate.approve receives mfa_factors=None (simple-tier)."""
        wiring, gate = _make_wiring_with_recording_gate()
        proposal_id = uuid4()
        await wiring.approve_action(
            proposal_id=proposal_id,
            sender_uid=_AUTHORIZED_UID,
            totp=None,
        )
        assert len(gate.approve_calls) == 1
        assert gate.approve_calls[0]["mfa_factors"] is None, (
            "Without totp, mfa_factors must be None so simple-tier tools pass through."
        )

    async def test_empty_string_totp_gives_none_mfa_factors(self) -> None:
        """Empty string totp is treated as absent (simple-tier path)."""
        wiring, gate = _make_wiring_with_recording_gate()
        proposal_id = uuid4()
        await wiring.approve_action(
            proposal_id=proposal_id,
            sender_uid=_AUTHORIZED_UID,
            totp="",
        )
        assert len(gate.approve_calls) == 1
        assert gate.approve_calls[0]["mfa_factors"] is None


# ---------------------------------------------------------------------------
# Bug #2: _translate_dbus_error must extract real reason from structured name
# ---------------------------------------------------------------------------


class TestTranslateDbusErrorReasonExtraction:
    """_translate_dbus_error must not collapse all ApprovalGateError reasons to 'proposal_invalid'."""

    def _make_dbus_error(self, name: str, message: str):
        """Build a dbus_fast DBusError with a structured name."""
        from dbus_fast import DBusError
        return DBusError(name, message)

    def test_mfa_required_reason_preserved(self) -> None:
        """org.hermes.Error.ApprovalGate.mfa_required → reason='mfa_required', not 'proposal_invalid'."""
        from hermes.shell_server.chat.dbus_control_plane_adapter import _translate_dbus_error
        from hermes.capabilities.infrastructure.sqlite_approval_gate import ApprovalGateError

        exc = self._make_dbus_error(
            "org.hermes.Error.ApprovalGate.mfa_required",
            "MFA inválida para aprobar tool mfa-tier 'skill_manage' (motivo=mfa_required).",
        )
        with pytest.raises(ApprovalGateError) as exc_info:
            _translate_dbus_error(exc)
        assert exc_info.value.reason == "mfa_required", (
            "Previously _translate_dbus_error always set reason='proposal_invalid', masking "
            "the real MFA failure and making a valid TOTP approval look like a missing proposal."
        )

    def test_invalid_totp_reason_preserved(self) -> None:
        """org.hermes.Error.ApprovalGate.invalid_totp → reason='invalid_totp'."""
        from hermes.shell_server.chat.dbus_control_plane_adapter import _translate_dbus_error
        from hermes.capabilities.infrastructure.sqlite_approval_gate import ApprovalGateError

        exc = self._make_dbus_error(
            "org.hermes.Error.ApprovalGate.invalid_totp",
            "TOTP incorrecto.",
        )
        with pytest.raises(ApprovalGateError) as exc_info:
            _translate_dbus_error(exc)
        assert exc_info.value.reason == "invalid_totp"

    def test_mfa_not_enrolled_reason_preserved(self) -> None:
        """org.hermes.Error.ApprovalGate.mfa_not_enrolled → reason='mfa_not_enrolled'."""
        from hermes.shell_server.chat.dbus_control_plane_adapter import _translate_dbus_error
        from hermes.capabilities.infrastructure.sqlite_approval_gate import ApprovalGateError

        exc = self._make_dbus_error(
            "org.hermes.Error.ApprovalGate.mfa_not_enrolled",
            "MFA no enrolado.",
        )
        with pytest.raises(ApprovalGateError) as exc_info:
            _translate_dbus_error(exc)
        assert exc_info.value.reason == "mfa_not_enrolled"

    def test_real_proposal_invalid_reason_preserved(self) -> None:
        """org.hermes.Error.ApprovalGate.proposal_invalid → reason='proposal_invalid' (correct case)."""
        from hermes.shell_server.chat.dbus_control_plane_adapter import _translate_dbus_error
        from hermes.capabilities.infrastructure.sqlite_approval_gate import ApprovalGateError

        exc = self._make_dbus_error(
            "org.hermes.Error.ApprovalGate.proposal_invalid",
            "proposal_id=X no existe o ya fue resuelta.",
        )
        with pytest.raises(ApprovalGateError) as exc_info:
            _translate_dbus_error(exc)
        assert exc_info.value.reason == "proposal_invalid"

    def test_legacy_fallback_still_maps_to_proposal_invalid(self) -> None:
        """Unstructured 'ApprovalGateError' in message text → fallback to 'proposal_invalid'."""
        from hermes.shell_server.chat.dbus_control_plane_adapter import _translate_dbus_error
        from hermes.capabilities.infrastructure.sqlite_approval_gate import ApprovalGateError

        # Old daemon that raised an untyped error with class name in the message.
        exc = self._make_dbus_error(
            "org.hermes.Error.Unknown",
            "ApprovalGateError: proposal not found",
        )
        with pytest.raises(ApprovalGateError) as exc_info:
            _translate_dbus_error(exc)
        assert exc_info.value.reason == "proposal_invalid"
