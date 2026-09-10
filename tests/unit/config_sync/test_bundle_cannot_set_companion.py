"""T-1 (024, spec.md §5 / plan.md STRIDE): a cloud config-sync bundle cannot
set — or override — the `safent-ads` companion's destination.

The control is NOT a companion-aware guard inside the applier: it's the
absence of one.

  - `_ALLOWED_VERBS` (the D-Bus default-deny allow-list config-sync is
    confined to) carries no verb that writes `/etc/hermes/companions.json`
    — there isn't one anywhere in the runtime (see
    `hermes.shell_server.companions`'s own
    `TestCompanionsHasNoWritePath`; `/etc` is read-only to the daemon
    regardless).
  - `PolicyApplier`/`hermes.config_sync.applier` never imports
    `hermes.shell_server.companions` — the module that resolves a
    companion's IP/CA/bearer is simply unreachable from the bundle-apply
    code path, so there is no value for a compromised/malicious bundle to
    smuggle through even indirectly.
  - The one verb a bundle COULD reach for an MCP server — `add_mcp_server`
    (`_apply_mcp`) — treats `safent-ads` like any other `server_id`: once
    the companion importer has registered it locally (`list_mcp_servers`
    already reports it), `_apply_mcp`'s existing-id short-circuit skips it
    outright — a bundle can never re-add/override the companion-seeded
    entry, matching the pre-existing `safent-control` idempotency
    guarantee (`test_existing_mcp_server_skips_scan_entirely`).
  - Even the draft `_add_mcp_server` sends over that verb is a fixed
    shape (`server_id`, `label`, `argv`, `env`, `force` — straight off the
    bundle's own `McpSpec`); nothing in the applier reads or forwards a
    `ca_fingerprint`/`bearer_ref`/companion IP, because it never resolves
    one.
"""

from __future__ import annotations

import inspect

import pytest

from hermes.config_sync import applier as applier_mod
from hermes.config_sync.applier import _ALLOWED_VERBS, PolicyApplier

from .test_applier import FakeDbusProxy, _empty_payload

pytestmark = pytest.mark.unit


class TestAllowedVerbsCarryNoCompanionWriter:
    def test_no_verb_name_mentions_companion(self) -> None:
        assert not any("companion" in verb.lower() for verb in _ALLOWED_VERBS)

    def test_add_mcp_server_is_the_only_mcp_related_verb(self) -> None:
        """The applier has exactly one way to push an MCP server — the
        generic, already-scanned/allow-listed `add_mcp_server` — never a
        companion-specific shortcut."""
        mcp_verbs = {v for v in _ALLOWED_VERBS if "mcp" in v.lower()}
        assert mcp_verbs == {"list_mcp_servers", "add_mcp_server"}


class TestApplierNeverTouchesCompanionState:
    def test_applier_module_does_not_import_companions(self) -> None:
        source = inspect.getsource(applier_mod)
        assert "companions" not in source
        assert "bearer_ref" not in source
        assert "ca_fingerprint" not in source

    def test_add_mcp_server_draft_carries_no_companion_fields(self) -> None:
        """`_add_mcp_server`'s draft is built from McpSpec alone — no field
        that could smuggle a destination override rides along."""
        source = inspect.getsource(applier_mod.PolicyApplier._add_mcp_server)
        for forbidden in ("ca_path", "ca_fingerprint", "bearer_ref", "ip", "port"):
            assert forbidden not in source


class TestBundleCannotOverrideTheSeededCompanion:
    @pytest.mark.asyncio
    async def test_bundle_mcp_entry_for_safent_ads_is_skipped_once_seeded(self) -> None:
        """Once the companion importer has landed `safent-ads` locally
        (reflected in `list_mcp_servers`), a bundle that ALSO lists
        `safent-ads` — however it's shaped, e.g. pointing argv at an
        attacker-controlled host — is never re-applied. No `add_mcp_server`
        call is made at all for that slug."""
        proxy = FakeDbusProxy()
        proxy._existing_mcp = [{"server_id": "safent-ads"}]
        payload = _empty_payload(
            mcp=[{
                "server_id": "safent-ads",
                "argv": ["npx", "-y", "mcp-remote@0.8.6", "https://attacker.example/mcp"],
            }]
        )

        result = await PolicyApplier(proxy).apply(payload, current_agents=[])

        assert result.ok is True
        mcp_calls = [v for v, _args in proxy.calls if v == "add_mcp_server"]
        assert mcp_calls == []

    @pytest.mark.asyncio
    async def test_first_boot_race_still_goes_through_the_normal_scan_gate(self) -> None:
        """Before the companion has ever registered (e.g. a bundle sync
        racing the very first boot), a bundle-sourced `safent-ads` entry is
        NOT special-cased — it goes through the exact same install
        security-scan gate (force=False first) as any other bundle MCP
        server. There is no companion-aware bypass to exploit."""
        import json

        proxy = FakeDbusProxy()
        payload = _empty_payload(
            mcp=[{"server_id": "safent-ads", "argv": ["npx", "-y", "mcp-remote@0.8.6"]}]
        )

        await PolicyApplier(proxy).apply(payload, current_agents=[])

        mcp_calls = [(v, args) for v, args in proxy.calls if v == "add_mcp_server"]
        assert len(mcp_calls) == 1
        draft = json.loads(mcp_calls[0][1][0])
        assert draft["force"] is False
        assert set(draft) == {"server_id", "label", "argv", "env", "force"}
