"""companion_reload_cli (028 T017) — the host-triggered hot-reload of a
companion's MCP presence, invoked via `podman exec -u hermes $NAME python3
-m hermes.shell_server.companion_reload_cli reload <slug>` by `safent
companion install|repair` right after a successful provisioning run.

Same structure as tests/unit/shell_server/test_brake_release_cli.py:
  - `_reload_companion_presence()` calls exactly
    `org.hermes.Runtime1.ReloadCompanionPresence(slug)` on the well-known
    bus name/object path, decodes the JSON result.
  - `cmd_reload()` maps a truthy `ok` to exit 0 + "[ok]", a falsy `ok` (or
    any raised exception) to exit 1 + a clear stderr message — fail-closed:
    the caller (safent's own porcelain reload step) must never see success
    unless the daemon actually reported it.
"""

from __future__ import annotations

import asyncio
import json

import dbus_fast.aio
import pytest

from hermes.shell_server import companion_reload_cli as cli

pytestmark = pytest.mark.unit

_SLUG = "safent-ads"


class _FakeInterface:
    def __init__(self, *, result: dict | None = None, exc: Exception | None = None) -> None:
        self._result = result if result is not None else {"ok": True, "state": "ready"}
        self._exc = exc
        self.calls = 0
        self.slugs: list[str] = []

    async def call_reload_companion_presence(self, slug: str) -> str:
        self.calls += 1
        self.slugs.append(slug)
        if self._exc is not None:
            raise self._exc
        return json.dumps(self._result)


class _FakeProxyObject:
    def __init__(self, iface: _FakeInterface) -> None:
        self._iface = iface

    def get_interface(self, name: str) -> _FakeInterface:
        assert name == "org.hermes.Runtime1"
        return self._iface


class _FakeBus:
    def __init__(self, iface: _FakeInterface) -> None:
        self._iface = iface
        self.connected = True
        self.disconnect_called = False
        self.introspected_with: tuple[str, str] | None = None

    async def connect(self) -> "_FakeBus":
        return self

    async def introspect(self, well_known_name: str, object_path: str) -> object:
        self.introspected_with = (well_known_name, object_path)
        return object()

    def get_proxy_object(
        self, well_known_name: str, object_path: str, _introspection: object
    ) -> _FakeProxyObject:
        assert (well_known_name, object_path) == self.introspected_with
        return _FakeProxyObject(self._iface)

    def disconnect(self) -> None:
        self.disconnect_called = True


def _install_fake_bus(monkeypatch: pytest.MonkeyPatch, iface: _FakeInterface) -> dict[str, _FakeBus]:
    bus_holder: dict[str, _FakeBus] = {}

    def _fake_message_bus(*, bus_type: object) -> _FakeBus:
        bus = _FakeBus(iface)
        bus_holder["bus"] = bus
        return bus

    monkeypatch.setattr(dbus_fast.aio, "MessageBus", _fake_message_bus)
    return bus_holder


class TestReloadCallsTheExactDbusVerb:
    def test_calls_reload_companion_presence_with_the_slug_and_disconnects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        iface = _FakeInterface(result={"ok": True, "state": "ready", "reachable": True})
        holder = _install_fake_bus(monkeypatch, iface)

        result = asyncio.run(cli._reload_companion_presence(_SLUG))

        assert result == {"ok": True, "state": "ready", "reachable": True}
        assert iface.calls == 1
        assert iface.slugs == [_SLUG]
        bus = holder["bus"]
        assert bus.introspected_with == ("org.hermes.Runtime", "/org/hermes/Runtime")
        assert bus.disconnect_called is True

    def test_a_dbus_error_propagates_to_the_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        iface = _FakeInterface(exc=RuntimeError("UID 880 required"))
        _install_fake_bus(monkeypatch, iface)

        with pytest.raises(RuntimeError, match="UID 880"):
            asyncio.run(cli._reload_companion_presence(_SLUG))


class TestCmdReloadExitCodesAndMessages:
    def test_ok_true_prints_ok_and_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "_reload_companion_presence", _async_returning({"ok": True}))

        rc = cli.cmd_reload(_SLUG)

        assert rc == 0
        assert "[ok]" in capsys.readouterr().out

    def test_ok_false_is_a_clear_failure_not_a_silent_ok(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            cli, "_reload_companion_presence",
            _async_returning({"ok": False, "reason": "not_installed"}),
        )

        rc = cli.cmd_reload(_SLUG)

        captured = capsys.readouterr()
        assert rc == 1
        assert "[ok]" not in captured.out
        assert "not_installed" in captured.err

    def test_an_exception_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def _raise(_slug: str) -> dict:
            raise RuntimeError("UID 880 required")

        monkeypatch.setattr(cli, "_reload_companion_presence", _raise)

        rc = cli.cmd_reload(_SLUG)

        captured = capsys.readouterr()
        assert rc == 1
        assert "Could not reload" in captured.err
        assert "UID 880" in captured.err


class TestMainDispatch:
    def test_reload_subcommand_invokes_cmd_reload_with_the_slug(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(cli, "cmd_reload", lambda slug: calls.append(slug) or 0)

        rc = cli.main(["reload", _SLUG])

        assert rc == 0
        assert calls == [_SLUG]

    def test_unknown_subcommand_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["bogus"])

    def test_reload_without_a_slug_is_rejected_by_argparse(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["reload"])


def _async_returning(value: dict):
    async def _inner(_slug: str) -> dict:
        return value

    return _inner
