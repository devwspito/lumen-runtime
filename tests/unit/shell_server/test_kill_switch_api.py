"""025 Top-KILL — POST/GET /api/v1/security/kill-switch REST surface.

Contract (owner-sovereign brake):
  - Engaging (engaged=true) needs NOTHING beyond the operator bearer that
    already fronts this route — one click, it's a brake.
  - Releasing (engaged=false) is a sovereign action: requires owner proof —
    the TOTP require_owner_mfa gate when MFA is enrolled (401/403 typed on
    missing/bad code, same as every other posture change), OR — when MFA
    isn't enrolled — the device password via the SAME PAM root-helper path
    POST /tailnet/disconnect uses (025 hallazgo C: a brake engaged before
    the owner ever enrolled TOTP used to be a dead end with no release path
    at all).
  - GET reflects the daemon's persisted state, fail-soft to {engaged: false}
    when the daemon is unavailable (never 503 on a status read).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.cowork import security_api as security_api_mod
from hermes.shell_server.cowork.security_api import create_security_router
from hermes.shell_server.security.mfa import MfaStore, totp_now
from hermes.tasks.control_plane.domain.ports import AgentUnavailable

pytestmark = pytest.mark.unit


def _proxy() -> MagicMock:
    p = MagicMock()
    p.call_bool = AsyncMock(return_value=True)
    p.call_dict = AsyncMock(return_value={
        "engaged": False, "reason": None, "changed_by": None, "changed_at": None,
    })
    return p


def _make_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, proxy: MagicMock) -> tuple[FastAPI, MfaStore]:
    mfa_store = MfaStore(store_dir=tmp_path / "mfa")
    monkeypatch.setattr(security_api_mod, "MfaStore", lambda: mfa_store)
    app = FastAPI()
    app.state.dbus_proxy = proxy
    app.include_router(create_security_router())
    return app, mfa_store


class TestEngageNeedsNoMfa:
    def test_engage_succeeds_without_totp(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.post("/api/v1/security/kill-switch", json={"engaged": True, "reason": "algo raro"})

        assert r.status_code == 200
        assert r.json() == {"ok": True, "engaged": True}
        proxy.call_bool.assert_awaited_once_with("pause", "algo raro")

    def test_engage_works_even_when_owner_never_enrolled_mfa(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The brake must work even for an owner who hasn't set up TOTP yet."""
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.post("/api/v1/security/kill-switch", json={"engaged": True})

        assert r.status_code == 200


class TestReleaseRequiresOwnerMfa:
    def test_release_without_enrollment_and_no_password_is_403(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Regression (025 hallazgo C): this used to be mfa_not_enrolled with
        NO way out (TOTP could never exist). Now it falls through to the
        device-password fallback, which fails closed on an empty password —
        still 403, but a DIFFERENT code that points at the real path forward."""
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.post("/api/v1/security/kill-switch", json={"engaged": False})

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "invalid_device_password"
        proxy.call_bool.assert_not_called()

    def test_release_with_wrong_totp_is_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        mfa.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/security/kill-switch", json={"engaged": False, "totp": "000000"}
        )

        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "invalid_totp"
        proxy.call_bool.assert_not_called()

    def test_release_with_valid_totp_calls_resume(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/security/kill-switch",
            json={"engaged": False, "totp": totp_now(secret)},
        )

        assert r.status_code == 200
        assert r.json() == {"ok": True, "engaged": False}
        # Security review 2026-09-10 (MEDIUM finding): the "totp" reason
        # travels to the signed AGENT_RESUMED entry, distinguishing this
        # MFA-verified release from `safent brake release`'s "host_cli".
        proxy.call_bool.assert_awaited_once_with("resume", "totp")


class TestReleaseDevicePasswordFallback:
    """025 hallazgo C: MFA not enrolled -> release via device password,
    reusing the tailnet-disconnect PAM root-helper path
    (security_api_mod._verify_device_password). "Fake PAM" = monkeypatch
    that one async function; the actual PAM subprocess lives in the ops
    script (tests/unit/ops/test_tailscale_control.py) and is out of scope
    for this REST-layer test."""

    def test_not_enrolled_wrong_password_is_403(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        monkeypatch.setattr(
            security_api_mod, "_verify_device_password", AsyncMock(return_value=False)
        )
        client = TestClient(app)

        r = client.post(
            "/api/v1/security/kill-switch",
            json={"engaged": False, "device_password": "wrong"},
        )

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "invalid_device_password"
        proxy.call_bool.assert_not_called()

    def test_not_enrolled_right_password_releases_and_is_audited(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        verify = AsyncMock(return_value=True)
        monkeypatch.setattr(security_api_mod, "_verify_device_password", verify)
        client = TestClient(app)

        r = client.post(
            "/api/v1/security/kill-switch",
            json={"engaged": False, "reason": "olvidé el TOTP", "device_password": "correct-horse"},
        )

        assert r.status_code == 200
        assert r.json() == {"ok": True, "engaged": False}
        verify.assert_awaited_once_with("correct-horse")
        # resume() is the SAME D-Bus call the TOTP path makes — the daemon's
        # AgentStatePort.resume(by=, reason=) records changed_by/reason/
        # changed_at there; no separate audit write needed on this path.
        # "device_password" (not "totp") is what distinguishes the two on
        # the signed AGENT_RESUMED entry (security review 2026-09-10).
        proxy.call_bool.assert_awaited_once_with("resume", "device_password")

    def test_enrolled_ignores_device_password_totp_path_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When MFA IS enrolled, a device_password in the body must be
        irrelevant — the TOTP gate is the only path, exactly as before."""
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        verify = AsyncMock(return_value=True)
        monkeypatch.setattr(security_api_mod, "_verify_device_password", verify)
        client = TestClient(app)

        r = client.post(
            "/api/v1/security/kill-switch",
            json={"engaged": False, "device_password": "irrelevant", "totp": totp_now(secret)},
        )

        assert r.status_code == 200
        assert r.json() == {"ok": True, "engaged": False}
        verify.assert_not_awaited()
        proxy.call_bool.assert_awaited_once_with("resume", "totp")

    def test_enrolled_bad_totp_still_401_even_with_device_password(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        mfa.enroll()
        verify = AsyncMock(return_value=True)
        monkeypatch.setattr(security_api_mod, "_verify_device_password", verify)
        client = TestClient(app)

        r = client.post(
            "/api/v1/security/kill-switch",
            json={"engaged": False, "device_password": "irrelevant", "totp": "000000"},
        )

        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "invalid_totp"
        verify.assert_not_awaited()
        proxy.call_bool.assert_not_called()


class TestVerifyDevicePasswordStagingAndPoll:
    """Exercises the REAL _verify_device_password glue (staging format + the
    poll loop) end to end, against a tmp control dir — no REST, no ops
    script (that PAM subprocess is tested in test_tailscale_control.py).
    Simulates the root helper by writing the result file directly."""

    async def test_stages_the_kill_switch_release_action_and_reads_ok_true(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import asyncio
        import json

        import hermes.shell_server.tailnet.api as tailnet_api_mod

        control_dir = tmp_path / "tailscale-control"
        control_dir.mkdir(mode=0o700)
        monkeypatch.setattr(tailnet_api_mod, "_DEFAULT_CONTROL_DIR", control_dir)

        async def _root_helper_writes_result() -> None:
            await asyncio.sleep(0.05)
            (control_dir / "kill-switch-result.json").write_text(
                '{"ok": true, "at": 1.0}', encoding="utf-8",
            )

        task = asyncio.ensure_future(_root_helper_writes_result())
        try:
            ok = await security_api_mod._verify_device_password("correct")
        finally:
            await task

        assert ok is True
        staged = json.loads((control_dir / "request.json").read_text(encoding="utf-8"))
        assert staged["action"] == "kill_switch_release"
        assert staged["password"] == "correct"

    async def test_ok_false_result_is_reported_as_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import asyncio

        import hermes.shell_server.tailnet.api as tailnet_api_mod

        control_dir = tmp_path / "tailscale-control"
        control_dir.mkdir(mode=0o700)
        monkeypatch.setattr(tailnet_api_mod, "_DEFAULT_CONTROL_DIR", control_dir)

        async def _root_helper_writes_result() -> None:
            await asyncio.sleep(0.05)
            (control_dir / "kill-switch-result.json").write_text(
                '{"ok": false, "at": 1.0}', encoding="utf-8",
            )

        task = asyncio.ensure_future(_root_helper_writes_result())
        try:
            ok = await security_api_mod._verify_device_password("wrong")
        finally:
            await task

        assert ok is False

    async def test_no_result_file_ever_appears_times_out_to_false(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import hermes.shell_server.tailnet.api as tailnet_api_mod

        control_dir = tmp_path / "tailscale-control"
        control_dir.mkdir(mode=0o700)
        monkeypatch.setattr(tailnet_api_mod, "_DEFAULT_CONTROL_DIR", control_dir)
        monkeypatch.setattr(security_api_mod, "_DEVICE_PASSWORD_VERIFY_TIMEOUT_S", 0.2)
        monkeypatch.setattr(security_api_mod, "_DEVICE_PASSWORD_POLL_INTERVAL_S", 0.05)

        ok = await security_api_mod._verify_device_password("whatever")

        assert ok is False

    async def test_empty_password_never_stages_anything(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import hermes.shell_server.tailnet.api as tailnet_api_mod

        control_dir = tmp_path / "tailscale-control"
        control_dir.mkdir(mode=0o700)
        monkeypatch.setattr(tailnet_api_mod, "_DEFAULT_CONTROL_DIR", control_dir)

        ok = await security_api_mod._verify_device_password("")

        assert ok is False
        assert not (control_dir / "request.json").exists()


class TestGetKillSwitchStatus:
    def test_get_reflects_daemon_status(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        proxy = _proxy()
        proxy.call_dict = AsyncMock(return_value={
            "engaged": True, "reason": "freno", "changed_by": "op-1", "changed_at": "2026-09-10T00:00:00Z",
        })
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.get("/api/v1/security/kill-switch")

        assert r.status_code == 200
        assert r.json()["engaged"] is True
        assert r.json()["reason"] == "freno"

    def test_get_fails_soft_when_daemon_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        proxy.call_dict = AsyncMock(side_effect=AgentUnavailable("daemon down"))
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.get("/api/v1/security/kill-switch")

        assert r.status_code == 200
        assert r.json()["engaged"] is False
