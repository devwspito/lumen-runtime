"""Unit tests for the managed-remote-endpoints REST surface on the MCP router.

Coverage:
  - GET /api/v1/mcp/managed-remote-endpoints — reads the local JSON store
    directly (no D-Bus), fail-soft to {} via the module's own load function.
  - PUT /api/v1/mcp/managed-remote-endpoints/{slug} — proxies
    set_managed_remote_endpoint(slug, url); pass-through of {ok:false, error}
    (daemon validation), 503 on AgentUnavailable.
  - POST /api/v1/mcp/managed-remote/{slug}/connect — two-step convenience:
    set_managed_remote_endpoint then add_mcp_server with the mcp-remote argv;
    short-circuits (never calls add_mcp_server) when step 1 fails; force
    pass-through to the add_mcp_server draft.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.cowork.mcp_api import create_mcp_router
from hermes.tasks.control_plane.domain.ports import AgentUnavailable

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(proxy: MagicMock) -> FastAPI:
    app = FastAPI()
    app.state.dbus_proxy = proxy
    app.include_router(create_mcp_router())
    return app


def _proxy(*, mutator_return=None, mutator_side_effect=None) -> MagicMock:
    p = MagicMock()
    if mutator_side_effect is not None:
        p.call_mutator = AsyncMock(side_effect=mutator_side_effect)
    else:
        p.call_mutator = AsyncMock(return_value=mutator_return or {"ok": True})
    return p


# ---------------------------------------------------------------------------
# GET /api/v1/mcp/managed-remote-endpoints
# ---------------------------------------------------------------------------


class TestListManagedRemoteEndpoints:
    def test_returns_stored_endpoints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "hermes.shell_server.cowork.mcp_api.load_managed_remote_endpoints",
            lambda: {"safent-ads": "https://ads.tenant.ts.net/mcp"},
        )
        client = TestClient(_make_app(_proxy()))
        r = client.get("/api/v1/mcp/managed-remote-endpoints")
        assert r.status_code == 200
        assert r.json() == {"endpoints": {"safent-ads": "https://ads.tenant.ts.net/mcp"}}

    def test_empty_store_returns_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "hermes.shell_server.cowork.mcp_api.load_managed_remote_endpoints",
            dict,
        )
        client = TestClient(_make_app(_proxy()))
        r = client.get("/api/v1/mcp/managed-remote-endpoints")
        assert r.status_code == 200
        assert r.json() == {"endpoints": {}}

    def test_does_not_call_dbus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "hermes.shell_server.cowork.mcp_api.load_managed_remote_endpoints",
            dict,
        )
        p = _proxy()
        client = TestClient(_make_app(p))
        client.get("/api/v1/mcp/managed-remote-endpoints")
        p.call_mutator.assert_not_called()


# ---------------------------------------------------------------------------
# PUT /api/v1/mcp/managed-remote-endpoints/{slug}
# ---------------------------------------------------------------------------


class TestSetManagedRemoteEndpoint:
    def test_success_calls_correct_verb_with_slug_and_url(self) -> None:
        p = _proxy(mutator_return={"ok": True})
        client = TestClient(_make_app(p))
        r = client.put(
            "/api/v1/mcp/managed-remote-endpoints/safent-ads",
            json={"url": "https://ads.tenant.ts.net/mcp"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        p.call_mutator.assert_called_once_with(
            "set_managed_remote_endpoint", "safent-ads", "https://ads.tenant.ts.net/mcp"
        )

    def test_daemon_validation_error_passed_through(self) -> None:
        p = _proxy(mutator_return={"ok": False, "error": "managed_remote endpoint must use https://"})
        client = TestClient(_make_app(p))
        r = client.put(
            "/api/v1/mcp/managed-remote-endpoints/safent-ads",
            json={"url": "http://ads.example.com"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False

    def test_unknown_slug_error_passed_through(self) -> None:
        p = _proxy(mutator_return={
            "ok": False,
            "error": "slug 'unknown' no es un servidor MANAGED_REMOTE conocido",
        })
        client = TestClient(_make_app(p))
        r = client.put(
            "/api/v1/mcp/managed-remote-endpoints/unknown",
            json={"url": "https://example.com"},
        )
        assert r.json()["ok"] is False

    def test_503_on_agent_unavailable(self) -> None:
        p = _proxy(mutator_side_effect=AgentUnavailable("daemon down"))
        client = TestClient(_make_app(p))
        r = client.put(
            "/api/v1/mcp/managed-remote-endpoints/safent-ads",
            json={"url": "https://ads.tenant.ts.net/mcp"},
        )
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "agent_unavailable"

    def test_empty_url_rejected_by_pydantic(self) -> None:
        p = _proxy()
        client = TestClient(_make_app(p))
        r = client.put("/api/v1/mcp/managed-remote-endpoints/safent-ads", json={"url": ""})
        assert r.status_code == 422
        p.call_mutator.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/v1/mcp/managed-remote/{slug}/connect
# ---------------------------------------------------------------------------


class TestConnectManagedRemote:
    def test_happy_path_sets_endpoint_then_adds_server(self) -> None:
        p = _proxy(
            mutator_side_effect=[
                {"ok": True},
                {"ok": True, "tool_count": 5},
            ]
        )
        client = TestClient(_make_app(p))
        r = client.post(
            "/api/v1/mcp/managed-remote/safent-ads/connect",
            json={"url": "https://ads.tenant.ts.net/mcp"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "tool_count": 5}
        assert p.call_mutator.call_count == 2

        first_call, second_call = p.call_mutator.call_args_list
        assert first_call.args == (
            "set_managed_remote_endpoint", "safent-ads", "https://ads.tenant.ts.net/mcp",
        )
        assert second_call.args[0] == "add_mcp_server"

    def test_add_mcp_server_draft_shape(self) -> None:
        import json as _json

        p = _proxy(
            mutator_side_effect=[
                {"ok": True},
                {"ok": True, "tool_count": 3},
            ]
        )
        client = TestClient(_make_app(p))
        client.post(
            "/api/v1/mcp/managed-remote/safent-ads/connect",
            json={"url": "https://ads.tenant.ts.net/mcp", "force": True},
        )
        _, second_call = p.call_mutator.call_args_list
        draft = _json.loads(second_call.args[1])
        assert draft["server_id"] == "safent-ads"
        assert draft["argv"] == ["npx", "-y", "mcp-remote", "https://ads.tenant.ts.net/mcp"]
        assert draft["label"] == "Safent Ads"
        assert draft["force"] is True

    def test_endpoint_rejection_short_circuits_before_add(self) -> None:
        p = _proxy(mutator_return={"ok": False, "error": "managed_remote endpoint must use https://"})
        client = TestClient(_make_app(p))
        r = client.post(
            "/api/v1/mcp/managed-remote/safent-ads/connect",
            json={"url": "http://ads.example.com"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False
        p.call_mutator.assert_called_once()

    def test_503_on_agent_unavailable_during_set_endpoint(self) -> None:
        p = _proxy(mutator_side_effect=AgentUnavailable("daemon down"))
        client = TestClient(_make_app(p))
        r = client.post(
            "/api/v1/mcp/managed-remote/safent-ads/connect",
            json={"url": "https://ads.tenant.ts.net/mcp"},
        )
        assert r.status_code == 503
        p.call_mutator.assert_called_once()

    def test_503_on_agent_unavailable_during_add(self) -> None:
        p = _proxy(
            mutator_side_effect=[
                {"ok": True},
                AgentUnavailable("daemon down"),
            ]
        )
        client = TestClient(_make_app(p))
        r = client.post(
            "/api/v1/mcp/managed-remote/safent-ads/connect",
            json={"url": "https://ads.tenant.ts.net/mcp"},
        )
        assert r.status_code == 503
        assert p.call_mutator.call_count == 2

    def test_blocked_scan_result_passed_through(self) -> None:
        blocked = {
            "ok": False, "blocked": True, "scan_id": "abc123",
            "verdict": "WARN", "error": "revisión requerida",
        }
        p = _proxy(mutator_side_effect=[{"ok": True}, blocked])
        client = TestClient(_make_app(p))
        r = client.post(
            "/api/v1/mcp/managed-remote/safent-ads/connect",
            json={"url": "https://ads.tenant.ts.net/mcp"},
        )
        assert r.status_code == 200
        assert r.json() == blocked

    def test_empty_url_rejected_by_pydantic(self) -> None:
        p = _proxy()
        client = TestClient(_make_app(p))
        r = client.post("/api/v1/mcp/managed-remote/safent-ads/connect", json={"url": ""})
        assert r.status_code == 422
        p.call_mutator.assert_not_called()
