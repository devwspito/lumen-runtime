"""set_managed_remote_endpoint D-Bus verb (item 3, ads-vertical).

Covers DbusRuntimeServiceWiring.set_managed_remote_endpoint:
  - Unauthorized sender_uid raises DbusAuthorizationError (never persists).
  - A slug outside _MANAGED_REMOTE_MCP_SLUGS is rejected (dead-setting guard).
  - An invalid URL (non-https, IP literal, wrong port) is rejected and NOT
    persisted (mirrors managed_remote_endpoints.save_managed_remote_endpoint).
  - A valid slug + https URL persists and round-trips via
    get_managed_remote_endpoint.
  - The verb is exported on the dbus-fast ServiceInterface (would otherwise
    silently resolve to None on the client proxy — the exact bug class
    test_dbus_verb_export_completeness.py guards against for config-sync
    verbs; this one is operator-only, so it isn't in _ALLOWED_VERBS, but the
    export itself still matters for the REST/D-Bus admin path to work).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hermes.agents_os.infrastructure.dbus_runtime_service import (
    DbusAuthorizationError,
    DbusRuntimeServiceWiring,
)
from hermes.tasks.testing.in_memory_agent_state import InMemoryAgentState

pytestmark = pytest.mark.unit

_OPERATOR_UID = 1000
_UNAUTHORIZED_UID = 9999


class _NullApprovalGate:
    """Unused stub — set_managed_remote_endpoint never touches the approval
    gate; only present to satisfy DbusRuntimeServiceWiring's constructor."""

    async def register_pending(self, **_kwargs: object) -> None: ...
    async def approve(self, **_kwargs: object) -> str:
        return ""
    async def reject(self, **_kwargs: object) -> None: ...
    async def verify_token(self, **_kwargs: object) -> bool:
        return False
    async def approved_token_for(self, *_args: object) -> str | None:
        return None


def _make_wiring() -> DbusRuntimeServiceWiring:
    return DbusRuntimeServiceWiring(
        agent_state=InMemoryAgentState(),
        approval_gate=_NullApprovalGate(),
        authorized_uids=frozenset({_OPERATOR_UID}),
    )


@pytest.fixture
def endpoints_path(tmp_path: Path):
    target = tmp_path / "managed-remote-endpoints.json"
    with patch(
        "hermes.shell_server.managed_remote_endpoints._ENDPOINTS_PATH", target
    ):
        yield target


class TestUnauthorized:
    def test_unauthorized_uid_raises(self, endpoints_path: Path) -> None:
        wiring = _make_wiring()
        with pytest.raises(DbusAuthorizationError):
            wiring.set_managed_remote_endpoint(
                slug="safent-ads",
                url="https://ads.tenant.ts.net/mcp",
                sender_uid=_UNAUTHORIZED_UID,
            )
        assert not endpoints_path.exists()


class TestSlugMustBeManagedRemote:
    def test_unknown_slug_rejected(self, endpoints_path: Path) -> None:
        wiring = _make_wiring()
        result = wiring.set_managed_remote_endpoint(
            slug="some-random-mcp",
            url="https://ads.tenant.ts.net/mcp",
            sender_uid=_OPERATOR_UID,
        )
        assert result["ok"] is False
        assert not endpoints_path.exists()

    def test_safent_ads_accepted(self, endpoints_path: Path) -> None:
        wiring = _make_wiring()
        result = wiring.set_managed_remote_endpoint(
            slug="safent-ads",
            url="https://ads.tenant.ts.net/mcp",
            sender_uid=_OPERATOR_UID,
        )
        assert result["ok"] is True
        assert endpoints_path.exists()


class TestUrlValidation:
    def test_non_https_rejected(self, endpoints_path: Path) -> None:
        wiring = _make_wiring()
        result = wiring.set_managed_remote_endpoint(
            slug="safent-ads",
            url="http://ads.tenant.ts.net/mcp",
            sender_uid=_OPERATOR_UID,
        )
        assert result["ok"] is False
        assert not endpoints_path.exists()

    def test_ip_literal_rejected(self, endpoints_path: Path) -> None:
        wiring = _make_wiring()
        result = wiring.set_managed_remote_endpoint(
            slug="safent-ads",
            url="https://169.254.169.254/mcp",
            sender_uid=_OPERATOR_UID,
        )
        assert result["ok"] is False
        assert not endpoints_path.exists()

    def test_wrong_port_rejected(self, endpoints_path: Path) -> None:
        wiring = _make_wiring()
        result = wiring.set_managed_remote_endpoint(
            slug="safent-ads",
            url="https://ads.tenant.ts.net:8080/mcp",
            sender_uid=_OPERATOR_UID,
        )
        assert result["ok"] is False
        assert not endpoints_path.exists()


class TestRoundTrip:
    def test_valid_endpoint_round_trips(self, endpoints_path: Path) -> None:
        from hermes.shell_server.managed_remote_endpoints import (
            get_managed_remote_endpoint,
        )

        wiring = _make_wiring()
        result = wiring.set_managed_remote_endpoint(
            slug="safent-ads",
            url="https://ads.tenant.ts.net/mcp",
            sender_uid=_OPERATOR_UID,
        )
        assert result["ok"] is True
        assert endpoints_path.exists()
        assert (
            get_managed_remote_endpoint("safent-ads") == "https://ads.tenant.ts.net/mcp"
        )


class TestDbusExport:
    def test_verb_exported_on_service_interface(self) -> None:
        pytest.importorskip("dbus_fast")
        from dbus_fast.proxy_object import BaseProxyInterface
        from dbus_fast.service import ServiceInterface

        from hermes.agents_os.infrastructure.dbus_fast_runtime_adapter import (
            Runtime1ServiceInterface,
        )

        class _StubWiring:
            pass

        iface = Runtime1ServiceInterface(wiring=_StubWiring())  # type: ignore[arg-type]
        methods = ServiceInterface._get_methods(iface)
        exported = {BaseProxyInterface._to_snake_case(m.name) for m in methods}
        assert "set_managed_remote_endpoint" in exported
