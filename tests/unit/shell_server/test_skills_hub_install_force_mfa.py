"""Regression (025 Top-4): `force:true` on the hub installer must clear the
SAME MFA bar as POST /security/decisions — not just a bearer token.

Bug: POST /api/v1/skills/hub/install with force=True forwarded straight to the
daemon's install_hub_skill(force=True), which cleared a FAIL scan verdict via
scan_svc.allow_target() with no TOTP check at all — any bearer-authenticated
caller (the agent's own tool-call surface included) could install a
skill the antivirus flagged FAIL, while the parallel /security/decisions path
for the exact same override correctly required require_owner_mfa.

Fix: skills_api.py's install route now calls require_owner_mfa (the SAME
helper security_api.py uses, not a re-implementation) before forwarding
force=True to the daemon. On the daemon side, _apply_owner_override_and_rescan
now routes the override through record_install_decision (the SAME mutator
POST /security/decisions calls) instead of poking ScanService directly, so the
decision lands in install_reviews (audit) too.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.cowork import skills_api as skills_api_mod
from hermes.shell_server.cowork.skills_api import InstallSkillRequest, create_skills_hub_router
from hermes.shell_server.security.mfa import MfaStore, totp_now

pytestmark = pytest.mark.unit


def _proxy() -> MagicMock:
    p = MagicMock()
    p.call_mutator = AsyncMock(return_value={"ok": True, "op_id": "op-1", "status": "pending"})
    return p


def _make_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, proxy: MagicMock) -> tuple[FastAPI, MfaStore]:
    mfa_store = MfaStore(store_dir=tmp_path / "mfa")
    # install_hub_skill instantiates MfaStore() itself (same as security_api.py) —
    # patch the class so both resolve to the SAME tmp-rooted store in the test.
    monkeypatch.setattr(skills_api_mod, "MfaStore", lambda: mfa_store)
    app = FastAPI()
    app.state.dbus_proxy = proxy
    app.include_router(create_skills_hub_router(tmp_path / "skills.db"))
    return app, mfa_store


class TestInstallSkillRequestSchema:
    def test_totp_field_defaults_to_none(self) -> None:
        req = InstallSkillRequest(identifier="pdf-tools")
        assert req.force is False
        assert req.totp is None

    def test_totp_field_accepted_with_force(self) -> None:
        req = InstallSkillRequest(identifier="pdf-tools", force=True, totp="123456")
        assert req.force is True
        assert req.totp == "123456"


class TestForceRequiresOwnerMfa:
    def test_force_without_enrollment_is_403(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.post("/api/v1/skills/hub/install", json={"identifier": "gitnexus-explorer", "force": True})

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "mfa_not_enrolled"
        proxy.call_mutator.assert_not_called()

    def test_force_with_wrong_totp_is_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        mfa.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": "gitnexus-explorer", "force": True, "totp": "000000"},
        )

        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "invalid_totp"
        proxy.call_mutator.assert_not_called()

    def test_force_with_missing_totp_is_401(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        mfa.enroll()
        client = TestClient(app)

        r = client.post("/api/v1/skills/hub/install", json={"identifier": "gitnexus-explorer", "force": True})

        assert r.status_code == 401
        proxy.call_mutator.assert_not_called()

    def test_force_with_valid_totp_installs_and_forwards_force(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": "gitnexus-explorer", "force": True, "totp": totp_now(secret)},
        )

        assert r.status_code == 202
        assert r.json() == {"ok": True, "op_id": "op-1", "status": "pending"}
        proxy.call_mutator.assert_awaited_once_with("install_hub_skill", "gitnexus-explorer", True)

    def test_force_false_never_checks_mfa(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No regression on the common path: a clean (PASS) install needs no TOTP."""
        proxy = _proxy()
        app, _mfa = _make_app(monkeypatch, tmp_path, proxy)
        client = TestClient(app)

        r = client.post("/api/v1/skills/hub/install", json={"identifier": "pdf-tools"})

        assert r.status_code == 202
        proxy.call_mutator.assert_awaited_once_with("install_hub_skill", "pdf-tools", False)
