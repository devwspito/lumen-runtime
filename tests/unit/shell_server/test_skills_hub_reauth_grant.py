"""Regression (matriz 10-sep, hallazgo A): skills hub `force` is unreachable
from the UI because a fresh `POST /security/decisions` approval already spent
the owner's single-use TOTP, and `POST /skills/hub/install force=true`
(`frontend/src/api/client.ts` `installSkill`) demanded a SECOND code —
re-sending the same one always failed `totp_replayed` (single-use, R32).

Fix: `POST /security/decisions` mints a short-lived (<=120s), single-use
re-auth grant bound to (identifier, action) right after a successful owner
override, returned as `reauth_grant` in the response body. `POST
/skills/hub/install` accepts that grant via the `X-Owner-Reauth-Grant`
header (never the body) as an alternative to a fresh TOTP — mirroring, not
duplicating, `require_owner_mfa` (see owner_mfa_gate.py).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.cowork import security_api as security_api_mod
from hermes.shell_server.cowork import skills_api as skills_api_mod
from hermes.shell_server.cowork.security_api import create_security_router
from hermes.shell_server.cowork.skills_api import create_skills_hub_router
from hermes.shell_server.security import owner_mfa_gate
from hermes.shell_server.security.mfa import MfaStore, totp_now

pytestmark = pytest.mark.unit


def _proxy() -> MagicMock:
    async def _call_mutator(op: str, *_args: object) -> dict:
        if op == "record_install_decision":
            return {"ok": True}
        return {"ok": True, "op_id": "op-1", "status": "pending"}

    p = MagicMock()
    p.call_mutator = AsyncMock(side_effect=_call_mutator)
    return p


@pytest.fixture(autouse=True)
def _clean_grant_store(monkeypatch: pytest.MonkeyPatch) -> None:
    # The grant store is a module-level dict (single uvicorn worker in prod) —
    # isolate each test so grants can't leak across cases.
    monkeypatch.setattr(owner_mfa_gate, "_reauth_grants", {})


def _make_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, proxy: MagicMock) -> tuple[FastAPI, MfaStore]:
    mfa_store = MfaStore(store_dir=tmp_path / "mfa")
    monkeypatch.setattr(security_api_mod, "MfaStore", lambda: mfa_store)
    monkeypatch.setattr(skills_api_mod, "MfaStore", lambda: mfa_store)
    app = FastAPI()
    app.state.dbus_proxy = proxy
    app.include_router(create_security_router())
    app.include_router(create_skills_hub_router(tmp_path / "skills.db"))
    return app, mfa_store


def _approve_decision(client: TestClient, totp: str, identifier: str = "official/research/gitnexus-explorer") -> dict:
    r = client.post(
        "/api/v1/security/decisions",
        json={
            "scan_id": "scan-1",
            "decision": "allow_once",
            "identifier": identifier,
            "kind": "skill",
            "score": 30,
            "verdict": "FAIL",
            "risks_json": "[]",
            "totp": totp,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestDecisionIssuesReauthGrant:
    def test_skill_override_response_carries_a_grant(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)

        body = _approve_decision(client, totp_now(secret))

        assert body["ok"] is True
        assert isinstance(body.get("reauth_grant"), str) and body["reauth_grant"]

    def test_non_skill_kind_gets_no_grant(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/security/decisions",
            json={
                "scan_id": "scan-2",
                "decision": "allow_once",
                "identifier": "some/mcp",
                "kind": "mcp",
                "score": 30,
                "verdict": "FAIL",
                "risks_json": "[]",
                "totp": totp_now(secret),
            },
        )

        assert r.status_code == 201
        assert "reauth_grant" not in r.json()


class TestInstallAcceptsReauthGrant:
    def test_grant_from_decision_installs_with_no_second_totp(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The exact UI flow: ONE TOTP on /security/decisions, then the hub
        install force=true call presents the grant header — no totp field."""
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)
        identifier = "official/research/gitnexus-explorer"

        decision = _approve_decision(client, totp_now(secret), identifier=identifier)

        r = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": identifier, "force": True},
            headers={"X-Owner-Reauth-Grant": decision["reauth_grant"]},
        )

        assert r.status_code == 202, r.text
        assert r.json() == {"ok": True, "op_id": "op-1", "status": "pending"}
        proxy.call_mutator.assert_awaited_with("install_hub_skill", identifier, True)

    def test_grant_is_single_use(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)
        identifier = "official/research/gitnexus-explorer"
        decision = _approve_decision(client, totp_now(secret), identifier=identifier)
        grant = decision["reauth_grant"]

        first = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": identifier, "force": True},
            headers={"X-Owner-Reauth-Grant": grant},
        )
        assert first.status_code == 202

        second = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": identifier, "force": True},
            headers={"X-Owner-Reauth-Grant": grant},
        )

        assert second.status_code == 401
        assert second.json()["detail"]["code"] == "invalid_reauth_grant"

    def test_expired_grant_is_refused(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        mfa.enroll()
        client = TestClient(app)
        identifier = "official/research/gitnexus-explorer"

        grant = owner_mfa_gate.issue_reauth_grant(identifier=identifier, action="install_hub_skill")
        # Fast-forward past the TTL by back-dating the stored expiry directly —
        # avoids sleeping 120s in a unit test while exercising the real check.
        got_id, got_action, _expires_at = owner_mfa_gate._reauth_grants[grant]
        owner_mfa_gate._reauth_grants[grant] = (got_id, got_action, 0.0)

        r = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": identifier, "force": True},
            headers={"X-Owner-Reauth-Grant": grant},
        )

        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "invalid_reauth_grant"
        proxy.call_mutator.assert_not_called()

    def test_grant_for_another_identifier_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)

        decision = _approve_decision(client, totp_now(secret), identifier="skill-a")

        r = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": "skill-b", "force": True},
            headers={"X-Owner-Reauth-Grant": decision["reauth_grant"]},
        )

        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "invalid_reauth_grant"
        # call_mutator WAS invoked once for record_install_decision (the
        # setup step) — assert install_hub_skill specifically never ran.
        for call in proxy.call_mutator.await_args_list:
            assert call.args[0] != "install_hub_skill"

    def test_unknown_grant_falls_back_to_401_not_totp_bypass(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        mfa.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": "gitnexus-explorer", "force": True},
            headers={"X-Owner-Reauth-Grant": "not-a-real-grant"},
        )

        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "invalid_reauth_grant"
        proxy.call_mutator.assert_not_called()

    def test_no_grant_still_accepts_a_fresh_totp(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Back-compat: a direct force install call with only a TOTP (no
        grant header) still works — require_owner_mfa_or_grant falls through."""
        proxy = _proxy()
        app, mfa = _make_app(monkeypatch, tmp_path, proxy)
        _uri, secret = mfa.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/skills/hub/install",
            json={"identifier": "gitnexus-explorer", "force": True, "totp": totp_now(secret)},
        )

        assert r.status_code == 202
        proxy.call_mutator.assert_awaited_with("install_hub_skill", "gitnexus-explorer", True)
