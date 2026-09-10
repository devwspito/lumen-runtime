"""025 Top-KILL — POST/GET /api/v1/security/kill-switch REST surface.

Contract (owner-sovereign brake):
  - Engaging (engaged=true) needs NOTHING beyond the operator bearer that
    already fronts this route — one click, it's a brake.
  - Releasing (engaged=false) is a sovereign action: requires the owner's
    TOTP, same require_owner_mfa gate as every other posture change
    (/security/decisions, /egress/mode) — 401/403 typed on missing/bad code.
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
    def test_release_without_enrollment_is_403(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.post("/api/v1/security/kill-switch", json={"engaged": False})

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "mfa_not_enrolled"
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
        proxy.call_bool.assert_awaited_once_with("resume")


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
