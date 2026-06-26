"""Unit tests — feature_guard middleware (Fase 3a).

Coverage:
  community edition (CE):
    - all /api/v1/* paths pass (no-op)
    - even unmapped endpoints pass

  associate edition:
    - /api/v1/chat always passes (always-allowed)
    - /api/v1/instance/* always passes (always-allowed)
    - GET /api/v1/providers → 403 when 'proveedores' not in views
    - GET /api/v1/providers → 200 when 'proveedores' in views
    - unmapped /api/v1/* endpoint → 403 (default-deny)
    - /healthz always passes

  cache:
    - second request reuses cache (store.edition called once)
"""

from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hermes.shell_server.instance.feature_guard import FeatureGuardMiddleware

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_store(edition: str, views: list[str]) -> MagicMock:
    """Build a fake SQLiteAssociationStore."""
    store = MagicMock()
    store.edition.return_value = edition
    store.is_associated.return_value = (edition == "associate")
    if edition == "associate":
        assoc = MagicMock()
        assoc.license = {"views": views}
        store.get.return_value = assoc
    else:
        store.get.return_value = None
    return store


@contextmanager
def _client(edition: str, views: list[str]):
    """Yield a TestClient with FeatureGuardMiddleware backed by a fake store."""
    store = _build_store(edition, views)
    app = FastAPI()
    fake_path = Path("/nonexistent/test.db")
    fake_vault = MagicMock()
    app.add_middleware(
        FeatureGuardMiddleware,
        db_path=fake_path,
        vault=fake_vault,
    )

    # Register test routes
    @app.get("/api/v1/providers")
    async def _providers() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/v1/chat/conversations")
    async def _chat() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/v1/instance/features")
    async def _features() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/v1/mcp/servers")
    async def _mcp() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/v1/unknown-future-endpoint")
    async def _unknown() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/healthz")
    async def _healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    with patch(
        "hermes.shell_server.instance.feature_guard.SQLiteAssociationStore",
        return_value=store,
    ):
        with TestClient(app) as client:
            yield client, store


# ---------------------------------------------------------------------------
# CE (community) — all paths pass
# ---------------------------------------------------------------------------


class TestCommunityEdition:
    def test_providers_passes_in_ce(self) -> None:
        with _client("community", []) as (c, _):
            assert c.get("/api/v1/providers").status_code == 200

    def test_mcp_passes_in_ce(self) -> None:
        with _client("community", []) as (c, _):
            assert c.get("/api/v1/mcp/servers").status_code == 200

    def test_unmapped_endpoint_passes_in_ce(self) -> None:
        with _client("community", []) as (c, _):
            assert c.get("/api/v1/unknown-future-endpoint").status_code == 200

    def test_healthz_passes_in_ce(self) -> None:
        with _client("community", []) as (c, _):
            assert c.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# Associate — always-allowed paths
# ---------------------------------------------------------------------------


class TestAssociateAlwaysAllowed:
    def test_chat_always_passes(self) -> None:
        with _client("associate", []) as (c, _):
            assert c.get("/api/v1/chat/conversations").status_code == 200

    def test_instance_features_always_passes(self) -> None:
        with _client("associate", []) as (c, _):
            assert c.get("/api/v1/instance/features").status_code == 200

    def test_healthz_always_passes(self) -> None:
        with _client("associate", []) as (c, _):
            assert c.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# Associate — feature gating
# ---------------------------------------------------------------------------


class TestAssociateFeatureGating:
    def test_providers_blocked_when_feature_absent(self) -> None:
        with _client("associate", ["chat", "coste"]) as (c, _):
            r = c.get("/api/v1/providers")
        assert r.status_code == 403
        assert "proveedores" in r.json()["detail"]

    def test_providers_passes_when_feature_present(self) -> None:
        with _client("associate", ["chat", "proveedores"]) as (c, _):
            r = c.get("/api/v1/providers")
        assert r.status_code == 200

    def test_mcp_blocked_when_feature_absent(self) -> None:
        with _client("associate", ["chat"]) as (c, _):
            r = c.get("/api/v1/mcp/servers")
        assert r.status_code == 403

    def test_mcp_passes_when_feature_present(self) -> None:
        with _client("associate", ["mcp"]) as (c, _):
            r = c.get("/api/v1/mcp/servers")
        assert r.status_code == 200

    def test_unmapped_endpoint_blocked_in_associate(self) -> None:
        """DEFAULT-DENY: unknown /api/v1/* path returns 403 in associate."""
        all_views = [
            "chat", "proveedores", "mcp", "skills", "integraciones",
            "programadas", "agentes", "seguridad", "memoria", "archivos", "coste",
        ]
        with _client("associate", all_views) as (c, _):
            r = c.get("/api/v1/unknown-future-endpoint")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Cache — store.edition called once per TTL window
# ---------------------------------------------------------------------------


class TestFeatureGuardCache:
    def test_store_consulted_once_for_multiple_requests(self) -> None:
        """The middleware caches edition+views; the store must not be hit every request."""
        with _client("associate", ["chat", "proveedores"]) as (c, store):
            c.get("/api/v1/providers")
            c.get("/api/v1/providers")
            c.get("/api/v1/providers")
        # edition() is called once per cache miss; three requests within the TTL
        # must result in exactly one call (the cache is warm for 2nd and 3rd).
        assert store.edition.call_count == 1


# ---------------------------------------------------------------------------
# License errors (domain layer) — imported and correctly named
# ---------------------------------------------------------------------------


class TestLicenseDomainErrors:
    def test_license_exceeded_is_runtime_error(self) -> None:
        from hermes.agents.domain.ports import LicenseExceeded
        assert issubclass(LicenseExceeded, RuntimeError)

    def test_license_expired_is_runtime_error(self) -> None:
        from hermes.agents.domain.ports import LicenseExpired
        assert issubclass(LicenseExpired, RuntimeError)

    def test_license_exceeded_message_preserved(self) -> None:
        from hermes.agents.domain.ports import LicenseExceeded
        exc = LicenseExceeded("max_agents=3, current=3")
        assert "max_agents=3" in str(exc)

    def test_license_expired_message_preserved(self) -> None:
        from hermes.agents.domain.ports import LicenseExpired
        exc = LicenseExpired("expired at 2024-01-01")
        assert "2024-01-01" in str(exc)
