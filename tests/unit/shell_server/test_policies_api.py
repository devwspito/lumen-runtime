"""specs/025-safent-repaso SEG-15 — MFA gating rules for the Policies API.

With `mfa_on_dangers:false` the UI used to send totp:"" for preset/tools
AND for the toggle itself — every mutation 401'd, including turning the
switch back ON, because `require_owner_mfa` was called unconditionally on
all four endpoints regardless of the posture the owner had just chosen.

The rule this pins (four combinations):
  1. mfa_on_dangers ON  + non-sovereign decision (preset/tools) -> TOTP required.
  2. mfa_on_dangers OFF + non-sovereign decision                -> no TOTP required.
  3. mfa_on_dangers toggle (either direction), MFA enrolled     -> TOTP always required.
  4. mfa_on_dangers toggle (either direction), MFA NOT enrolled -> no TOTP required
     (never a hard 403 — a fresh install must be able to flip its own switch).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.capabilities.tool_policy import ToolPolicyStore
from hermes.shell_server.cowork.policies_api import create_policies_router
from hermes.shell_server.security.mfa import MfaStore, totp_now

pytestmark = pytest.mark.unit


def _make_app(tmp_path: Path) -> tuple[FastAPI, ToolPolicyStore, MfaStore]:
    store = ToolPolicyStore(path=tmp_path / "policy.json")
    mfa_store = MfaStore(store_dir=tmp_path / "mfa")
    app = FastAPI()
    app.include_router(create_policies_router(policy=store, mfa=mfa_store))
    return app, store, mfa_store


# ---------------------------------------------------------------------------
# Combinations 1 & 2 — non-sovereign decisions follow mfa_on_dangers
# ---------------------------------------------------------------------------


class TestNonSovereignDecisionsFollowMfaOnDangers:
    def test_preset_requires_totp_when_mfa_on_dangers_is_on(self, tmp_path: Path) -> None:
        app, _store, mfa_store = _make_app(tmp_path)
        _uri, secret = mfa_store.enroll()
        client = TestClient(app)

        denied = client.post("/api/v1/policies/preset", json={"preset": "permisivo", "totp": ""})
        assert denied.status_code == 401

        allowed = client.post(
            "/api/v1/policies/preset",
            json={"preset": "permisivo", "totp": totp_now(secret)},
        )
        assert allowed.status_code == 200

    def test_preset_does_not_require_totp_when_mfa_on_dangers_is_off(self, tmp_path: Path) -> None:
        app, store, mfa_store = _make_app(tmp_path)
        mfa_store.enroll()
        store.set_mfa_on_dangers(False)
        client = TestClient(app)

        r = client.post("/api/v1/policies/preset", json={"preset": "permisivo", "totp": ""})

        assert r.status_code == 200
        assert r.json() == {"ok": True, "preset": "permisivo"}

    def test_tools_batch_does_not_require_totp_when_mfa_on_dangers_is_off(
        self, tmp_path: Path
    ) -> None:
        app, store, mfa_store = _make_app(tmp_path)
        mfa_store.enroll()
        store.set_mfa_on_dangers(False)
        client = TestClient(app)

        r = client.post(
            "/api/v1/policies/tools", json={"tools": {"web_search": False}, "totp": ""}
        )

        assert r.status_code == 200
        assert r.json() == {"ok": True, "count": 1}

    def test_single_tool_requires_totp_when_mfa_on_dangers_is_on(self, tmp_path: Path) -> None:
        app, _store, mfa_store = _make_app(tmp_path)
        mfa_store.enroll()
        client = TestClient(app)

        r = client.post(
            "/api/v1/policies/tool", json={"tool": "web_search", "enabled": False, "totp": ""}
        )

        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Combinations 3 & 4 — the mfa_on_dangers toggle is sovereign
# ---------------------------------------------------------------------------


class TestMfaOnDangersToggleIsSovereign:
    def test_turning_off_requires_totp_when_enrolled(self, tmp_path: Path) -> None:
        app, _store, mfa_store = _make_app(tmp_path)
        _uri, secret = mfa_store.enroll()
        client = TestClient(app)

        denied = client.post("/api/v1/policies/mfa_on_dangers", json={"enabled": False, "totp": ""})
        assert denied.status_code == 401

        allowed = client.post(
            "/api/v1/policies/mfa_on_dangers",
            json={"enabled": False, "totp": totp_now(secret)},
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"ok": True, "mfa_on_dangers": False}

    def test_turning_back_on_also_requires_totp_even_though_it_was_off(
        self, tmp_path: Path
    ) -> None:
        """The exact SEG-15 dead-end: re-enabling from an OFF state must
        still prompt for TOTP — it must NOT inherit the "no TOTP while off"
        rule that applies to preset/tools."""
        app, store, mfa_store = _make_app(tmp_path)
        _uri, secret = mfa_store.enroll()
        store.set_mfa_on_dangers(False)
        client = TestClient(app)

        denied = client.post("/api/v1/policies/mfa_on_dangers", json={"enabled": True, "totp": ""})
        assert denied.status_code == 401

        allowed = client.post(
            "/api/v1/policies/mfa_on_dangers",
            json={"enabled": True, "totp": totp_now(secret)},
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"ok": True, "mfa_on_dangers": True}

    def test_toggle_needs_no_totp_when_mfa_was_never_enrolled(self, tmp_path: Path) -> None:
        """A fresh install (no MFA enrolled yet) must be able to flip its own
        switch — a hard 403 here would permanently strand it."""
        app, _store, _mfa_store = _make_app(tmp_path)
        client = TestClient(app)

        r = client.post("/api/v1/policies/mfa_on_dangers", json={"enabled": True, "totp": ""})

        assert r.status_code == 200
        assert r.json() == {"ok": True, "mfa_on_dangers": True}
