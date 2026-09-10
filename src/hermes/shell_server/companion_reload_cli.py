"""companion_reload_cli — host-triggered hot-reload of a companion's MCP
presence, no daemon restart (028 T017).

Usage (inside the container, invoked by `safent companion install|repair`
on the HOST, right after a successful provisioning run):
  python3 -m hermes.shell_server.companion_reload_cli reload <slug>

Why this exists
----------------
`ReloadCompanionPresence` (contracts/sso.md-adjacent, T017) is allow-listed
on `org.hermes.Runtime1.conf` for the shell-server's own uid (`hermes`)
ONLY — the SAME boundary `MintCompanionOwnerAssertion` uses (the call
carries no human identity to authorize against, and it is a system
reconciliation step, not an operator action). The host-side `safent` CLI
has no Python/D-Bus client of its own; this thin wrapper is what `podman
exec -u hermes $NAME python3 -m hermes.shell_server.companion_reload_cli
reload <slug>` actually runs, reusing the exact D-Bus call shape
`brake_release_cli.py` already established for a host-only, no-REST-bearer
verb invocation — never a second implementation of the D-Bus client plumbing.

`safent companion install|repair` treats a failure here as best-effort
(029 CL-002: hot-reload is preferred, a transparent restart is an
acceptable fallback) — this CLI still exits non-zero on failure so the
caller can log it, it just must never be escalated to "the install failed".
"""

from __future__ import annotations

import argparse
import asyncio
import sys

_WELL_KNOWN_NAME = "org.hermes.Runtime"
_OBJECT_PATH = "/org/hermes/Runtime"
_INTERFACE_NAME = "org.hermes.Runtime1"
_DBUS_CALL_TIMEOUT_S = 15.0


async def _reload_companion_presence(slug: str) -> dict:
    """Call ReloadCompanionPresence(slug) on the system bus. Raises on any
    D-Bus/transport error — the caller turns that into a clear, non-zero-exit
    CLI failure."""
    import json  # noqa: PLC0415

    from dbus_fast import BusType  # noqa: PLC0415
    from dbus_fast.aio import MessageBus  # noqa: PLC0415

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        introspection = await bus.introspect(_WELL_KNOWN_NAME, _OBJECT_PATH)
        proxy = bus.get_proxy_object(_WELL_KNOWN_NAME, _OBJECT_PATH, introspection)
        iface = proxy.get_interface(_INTERFACE_NAME)
        raw = await asyncio.wait_for(
            iface.call_reload_companion_presence(slug), timeout=_DBUS_CALL_TIMEOUT_S
        )
        return dict(json.loads(raw))
    finally:
        if getattr(bus, "connected", False):
            bus.disconnect()


def cmd_reload(slug: str) -> int:
    try:
        result = asyncio.run(_reload_companion_presence(slug))
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller, never swallowed
        print(f"[x] Could not reload the companion's presence: {exc}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"[x] Reload reported failure: {result}", file=sys.stderr)
        return 1
    print(f"[ok] Companion '{slug}' presence reloaded: {result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="companion_reload_cli")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    reload_parser = sub.add_parser("reload", help="Reload a companion's MCP presence.")
    reload_parser.add_argument("slug")
    args = parser.parse_args(argv)

    if args.subcommand == "reload":
        return cmd_reload(args.slug)
    parser.print_usage(sys.stderr)  # pragma: no cover — argparse already exits on bad subcommand
    return 1


if __name__ == "__main__":
    sys.exit(main())
