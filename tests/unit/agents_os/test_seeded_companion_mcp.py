"""Seeded companion MCP (024, item 2) — `_SEEDED_MCP_SLUGS`, the companion
env auto-wire (`_autowire_companion_env`), the companion seed importer
(`_import_seed_companion_servers`), and the coarse companion status surfaced
by `list_mcp_servers` (`_seeded_companion_status`).

Reuses the Neus module-stub pattern from test_mcp_neus_single_source.py so
these tests never need the real `tools.*`/`hermes_cli.config` packages.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def _ensure_neus_stubs() -> None:
    if "tools" not in sys.modules:
        sys.modules["tools"] = types.ModuleType("tools")
    if "tools.mcp_tool_discovery" not in sys.modules:
        mod = types.ModuleType("tools.mcp_tool_discovery")
        mod.get_mcp_status = list  # type: ignore[attr-defined]
        mod.register_mcp_servers = lambda _servers: []  # type: ignore[attr-defined]
        sys.modules["tools.mcp_tool_discovery"] = mod
    if "tools.mcp_tool_config" not in sys.modules:
        cfg_mod = types.ModuleType("tools.mcp_tool_config")
        cfg_mod._load_mcp_config = dict  # type: ignore[attr-defined]
        sys.modules["tools.mcp_tool_config"] = cfg_mod
    if "hermes_cli" not in sys.modules:
        sys.modules["hermes_cli"] = types.ModuleType("hermes_cli")
    if "hermes_cli.config" not in sys.modules:
        mod = types.ModuleType("hermes_cli.config")
        mod.load_config = dict  # type: ignore[attr-defined]
        mod.save_config = lambda _cfg: None  # type: ignore[attr-defined]
        sys.modules["hermes_cli.config"] = mod


_ensure_neus_stubs()

from hermes.agents_os.infrastructure import dbus_runtime_service as svc  # noqa: E402
from hermes.agents_os.infrastructure.dbus_runtime_service import (  # noqa: E402
    _MANAGED_REMOTE_MCP_SLUGS,
    _SEEDED_MCP_SLUGS,
    _autowire_companion_env,
    _import_seed_companion_servers,
    _seeded_companion_status,
)
from hermes.shell_server.companions import CompanionEndpoint  # noqa: E402


def _endpoint(**overrides: object) -> CompanionEndpoint:
    fields = {
        "slug": "safent-ads",
        "url": "https://ads.safent.internal:8443/mcp",
        "host": "ads.safent.internal",
        "ip": "10.201.0.10",
        "port": 8443,
        "ca_path": "/etc/hermes/companions/ads-ca.crt",
        "ca_fingerprint": "sha256:" + "a" * 64,
        "bearer_ref": "file:/etc/hermes/companions/ads.bearer",
    }
    fields.update(overrides)
    return CompanionEndpoint(**fields)  # type: ignore[arg-type]


class TestSeededSlugsAreManagedRemote:
    """024 §1.4 — seeded (lifecycle) and MANAGED_REMOTE (trust) are two axes;
    every seeded slug MUST also be MANAGED_REMOTE (writes stay gated)."""

    def test_safent_ads_is_seeded(self) -> None:
        assert "safent-ads" in _SEEDED_MCP_SLUGS

    def test_every_seeded_slug_is_managed_remote(self) -> None:
        assert _SEEDED_MCP_SLUGS <= _MANAGED_REMOTE_MCP_SLUGS


class TestAutowireCompanionEnv:
    def test_fills_declared_empty_keys_from_the_companion(self) -> None:
        env = {"ADS_BEARER": "", "NODE_EXTRA_CA_CERTS": ""}
        endpoint = _endpoint()
        with (
            patch("hermes.shell_server.companions.get_companion", return_value=endpoint),
            patch("hermes.shell_server.companions.read_companion_bearer", return_value="tok-123"),
        ):
            _autowire_companion_env("safent-ads", env)
        assert env["ADS_BEARER"] == "tok-123"
        assert env["NODE_EXTRA_CA_CERTS"] == "/etc/hermes/companions/ads-ca.crt"

    def test_never_overwrites_a_non_empty_value(self) -> None:
        env = {"ADS_BEARER": "caller-set", "NODE_EXTRA_CA_CERTS": "/caller/ca.crt"}
        endpoint = _endpoint()
        with (
            patch("hermes.shell_server.companions.get_companion", return_value=endpoint),
            patch("hermes.shell_server.companions.read_companion_bearer", return_value="tok-123"),
        ):
            _autowire_companion_env("safent-ads", env)
        assert env["ADS_BEARER"] == "caller-set"
        assert env["NODE_EXTRA_CA_CERTS"] == "/caller/ca.crt"

    def test_noop_for_a_non_seeded_slug(self) -> None:
        env = {"ADS_BEARER": ""}
        with patch("hermes.shell_server.companions.get_companion") as get_companion:
            _autowire_companion_env("some-random-mcp", env)
        get_companion.assert_not_called()
        assert env == {"ADS_BEARER": ""}

    def test_noop_when_env_declares_neither_key(self) -> None:
        env = {"OTHER_KEY": ""}
        with patch("hermes.shell_server.companions.get_companion") as get_companion:
            _autowire_companion_env("safent-ads", env)
        get_companion.assert_not_called()

    def test_noop_when_companion_is_absent(self) -> None:
        env = {"ADS_BEARER": "", "NODE_EXTRA_CA_CERTS": ""}
        with patch("hermes.shell_server.companions.get_companion", return_value=None):
            _autowire_companion_env("safent-ads", env)
        assert env == {"ADS_BEARER": "", "NODE_EXTRA_CA_CERTS": ""}

    def test_fails_soft_when_companions_module_raises(self) -> None:
        env = {"ADS_BEARER": ""}
        with patch(
            "hermes.shell_server.companions.get_companion", side_effect=RuntimeError("boom")
        ):
            _autowire_companion_env("safent-ads", env)  # must not raise
        assert env == {"ADS_BEARER": ""}


class TestSeededCompanionStatus:
    def test_non_seeded_slug_has_no_status(self) -> None:
        assert _seeded_companion_status("excel", connected=True) is None

    def test_absent_companion_is_waiting_for_service(self) -> None:
        with patch("hermes.shell_server.companions.get_companion", return_value=None):
            assert _seeded_companion_status("safent-ads", connected=False) == "esperando_servicio"

    def test_connected_companion_is_ready(self) -> None:
        with patch("hermes.shell_server.companions.get_companion", return_value=_endpoint()):
            assert _seeded_companion_status("safent-ads", connected=True) == "listo"

    def test_valid_but_not_yet_connected_is_waiting_for_service(self) -> None:
        with patch("hermes.shell_server.companions.get_companion", return_value=_endpoint()):
            assert _seeded_companion_status("safent-ads", connected=False) == "esperando_servicio"


class TestImportSeedCompanionServers:
    def _reset_marker(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setattr(svc, "_COMPANION_SEED_MARKER", str(tmp_path / "marker.json"))

    def test_writes_a_neus_entry_when_the_companion_resolves(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        self._reset_marker(monkeypatch, tmp_path)
        written: dict = {}

        def fake_write(sid, argv, *, env=None, label=None, register=True):
            written["server_id"] = sid
            written["argv"] = argv
            written["env"] = env
            written["label"] = label
            written["register"] = register

        with (
            patch("hermes.shell_server.companions.get_companion", return_value=_endpoint()),
            patch("tools.mcp_tool_config._load_mcp_config", return_value={}),
            patch.object(svc, "_neus_write_mcp_entry", side_effect=fake_write),
        ):
            _import_seed_companion_servers()

        assert written["server_id"] == "safent-ads"
        assert written["argv"] == [
            "npx", "-y", "mcp-remote", "https://ads.safent.internal:8443/mcp",
        ]
        assert written["env"] == {"ADS_BEARER": "", "NODE_EXTRA_CA_CERTS": ""}
        assert written["register"] is False
        assert "safent-ads" in svc._read_companion_seed_marker()

    def test_absent_companion_writes_nothing_and_is_not_marked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        self._reset_marker(monkeypatch, tmp_path)
        with (
            patch("hermes.shell_server.companions.get_companion", return_value=None),
            patch("tools.mcp_tool_config._load_mcp_config", return_value={}),
            patch.object(svc, "_neus_write_mcp_entry") as write,
        ):
            _import_seed_companion_servers()

        write.assert_not_called()
        assert svc._read_companion_seed_marker() == set()

    def test_already_imported_slug_is_never_retried(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        self._reset_marker(monkeypatch, tmp_path)
        svc._write_companion_seed_marker({"safent-ads"})

        with (
            patch("hermes.shell_server.companions.get_companion", return_value=_endpoint()),
            patch("tools.mcp_tool_config._load_mcp_config", return_value={}),
            patch.object(svc, "_neus_write_mcp_entry") as write,
        ):
            _import_seed_companion_servers()

        write.assert_not_called()  # owner may have removed it — never resurrect

    def test_existing_neus_entry_is_not_overwritten(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        self._reset_marker(monkeypatch, tmp_path)
        neus_cfg = {"safent-ads": {"command": "npx", "args": ["-y", "mcp-remote", "https://legacy/mcp"]}}

        with (
            patch("hermes.shell_server.companions.get_companion", return_value=_endpoint()),
            patch("tools.mcp_tool_config._load_mcp_config", return_value=neus_cfg),
            patch.object(svc, "_neus_write_mcp_entry") as write,
        ):
            _import_seed_companion_servers()

        write.assert_not_called()
        assert "safent-ads" in svc._read_companion_seed_marker()  # still marked, no retry


class TestListMcpServersSurfacesCompanionStatus:
    def _make_wiring(self):
        from hermes.agents_os.infrastructure.dbus_runtime_service import (
            DbusRuntimeServiceWiring,
        )
        return DbusRuntimeServiceWiring(
            agent_state=None,
            approval_gate=None,
            authorized_uids=frozenset({1000}),
            work_queue=None,
            wake_signal=None,
        )

    @pytest.mark.asyncio
    async def test_seeded_slug_carries_companion_status(self) -> None:
        wiring = self._make_wiring()
        live_status = [{"name": "safent-ads", "connected": True, "tools": 5}]
        neus_cfg = {"safent-ads": {"command": "npx", "args": ["-y", "mcp-remote", "https://x/mcp"]}}

        with (
            patch("tools.mcp_tool_discovery.get_mcp_status", return_value=live_status),
            patch("tools.mcp_tool_config._load_mcp_config", return_value=neus_cfg),
            patch("hermes.shell_server.companions.get_companion", return_value=_endpoint()),
        ):
            result = await wiring.list_mcp_servers()

        assert len(result) == 1
        assert result[0]["companion_status"] == "listo"

    @pytest.mark.asyncio
    async def test_non_seeded_slug_carries_no_companion_status(self) -> None:
        wiring = self._make_wiring()
        neus_cfg = {"github": {"command": "npx", "args": ["-y", "@scope/pkg"]}}

        with (
            patch("tools.mcp_tool_discovery.get_mcp_status", return_value=[]),
            patch("tools.mcp_tool_config._load_mcp_config", return_value=neus_cfg),
        ):
            result = await wiring.list_mcp_servers()

        assert "companion_status" not in result[0]
