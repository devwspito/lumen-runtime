"""Owner decision (024, spec.md §5): a cloud policy_overlay `approval:"auto"`
may only NARROW what the classifier marked for a MANAGED_REMOTE slug, never
WIDEN it — a cloud that could auto-approve a spend/write tool on safent-ads
is a confused deputy over the owner's money.

Covers `_mcp_slug_of_qualified_tool`, `_policy_overlay_widens_managed_remote_
write`, `_validate_policy_overlay_shape`, and the end-to-end D-Bus trust
boundary `_parse_access_scope_json`.
"""

from __future__ import annotations

import pytest

from hermes.agents_os.infrastructure.dbus_runtime_service import (
    _mcp_slug_of_qualified_tool,
    _parse_access_scope_json,
    _policy_overlay_widens_managed_remote_write,
    _validate_policy_overlay_shape,
)

pytestmark = pytest.mark.unit


class TestMcpSlugOfQualifiedTool:
    def test_extracts_the_slug(self) -> None:
        slug = _mcp_slug_of_qualified_tool("mcp__safent-ads__apply_defensive_action")
        assert slug == "safent-ads"

    def test_none_for_a_native_tool(self) -> None:
        assert _mcp_slug_of_qualified_tool("terminal") is None

    def test_none_for_a_malformed_qualified_name(self) -> None:
        assert _mcp_slug_of_qualified_tool("mcp__onlyslug") is None

    def test_none_when_prefix_is_not_mcp(self) -> None:
        assert _mcp_slug_of_qualified_tool("other__safent-ads__list_agents") is None


class TestPolicyOverlayWidensManagedRemoteWrite:
    def test_true_for_auto_on_a_gated_write_tool(self) -> None:
        entry = {"approval": "auto"}
        assert _policy_overlay_widens_managed_remote_write(
            "mcp__safent-ads__pause_campaign", entry
        )

    def test_false_for_hitl_on_a_gated_write_tool(self) -> None:
        entry = {"approval": "hitl"}
        assert not _policy_overlay_widens_managed_remote_write(
            "mcp__safent-ads__pause_campaign", entry
        )

    def test_false_for_auto_on_an_already_auto_read_tool(self) -> None:
        entry = {"approval": "auto"}
        assert not _policy_overlay_widens_managed_remote_write(
            "mcp__safent-ads__list_campaigns", entry
        )

    def test_false_for_auto_on_a_slug_widened_write_prefix(self) -> None:
        # apply_defensive_action is explicitly widened for safent-ads
        # (_MANAGED_REMOTE_AUTO_PREFIXES) — already auto, "auto" is a no-op.
        entry = {"approval": "auto"}
        assert not _policy_overlay_widens_managed_remote_write(
            "mcp__safent-ads__apply_defensive_action", entry
        )

    def test_false_for_a_non_managed_remote_slug(self) -> None:
        entry = {"approval": "auto"}
        assert not _policy_overlay_widens_managed_remote_write(
            "mcp__some-user-added-server__delete_all", entry
        )

    def test_false_for_a_native_non_mcp_tool(self) -> None:
        entry = {"approval": "auto"}
        assert not _policy_overlay_widens_managed_remote_write("install_mcp", entry)

    def test_false_when_no_approval_key(self) -> None:
        assert not _policy_overlay_widens_managed_remote_write(
            "mcp__safent-ads__pause_campaign", {"enabled": True}
        )


class TestValidatePolicyOverlayShapeRejectsWidening:
    def test_rejects_auto_on_a_managed_remote_write(self) -> None:
        overlay = {"mcp__safent-ads__pause_campaign": {"approval": "auto"}}
        error = _validate_policy_overlay_shape(overlay)
        assert error is not None
        assert "ensancharía" in error

    def test_accepts_hitl_on_a_managed_remote_write(self) -> None:
        overlay = {"mcp__safent-ads__pause_campaign": {"approval": "hitl"}}
        assert _validate_policy_overlay_shape(overlay) is None

    def test_accepts_auto_on_a_managed_remote_read(self) -> None:
        overlay = {"mcp__safent-ads__list_campaigns": {"approval": "auto"}}
        assert _validate_policy_overlay_shape(overlay) is None

    def test_accepts_auto_on_an_unrelated_native_tool(self) -> None:
        overlay = {"web_search": {"approval": "auto"}}
        assert _validate_policy_overlay_shape(overlay) is None

    def test_rejects_auto_on_safent_control_write_too(self) -> None:
        overlay = {"mcp__safent-control__create_employee": {"approval": "auto"}}
        error = _validate_policy_overlay_shape(overlay)
        assert error is not None


class TestParseAccessScopeJsonRejectsWideningEndToEnd:
    def test_scope_json_with_widening_overlay_is_rejected(self) -> None:
        import json

        payload = json.dumps({
            "policy_overlay": {"mcp__safent-ads__pause_campaign": {"approval": "auto"}},
        })
        fields, error = _parse_access_scope_json(payload)
        assert fields == {}
        assert error is not None
        assert "ensancharía" in error

    def test_scope_json_with_narrowing_overlay_is_accepted(self) -> None:
        import json

        payload = json.dumps({
            "policy_overlay": {"mcp__safent-ads__pause_campaign": {"approval": "hitl"}},
        })
        fields, error = _parse_access_scope_json(payload)
        assert error is None
        assert fields["policy_overlay"] == {
            "mcp__safent-ads__pause_campaign": {"approval": "hitl"}
        }
