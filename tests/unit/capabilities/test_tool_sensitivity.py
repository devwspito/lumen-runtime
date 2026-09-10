"""Tests for tool_sensitivity — SENSITIVE tier classification (Fase 2 Phase 4a).

Covers each SensitivityCategory positive + negative, plus fail-soft behaviour
on malformed args. Pure function — no DB, no I/O.
"""

from __future__ import annotations

import pytest

from hermes.capabilities.tool_sensitivity import SensitivityCategory, sensitivity

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# PII_READ
# ---------------------------------------------------------------------------


class TestPiiRead:
    def test_read_file_of_sensitive_path_is_pii_read(self) -> None:
        result = sensitivity("read_file", {"path": "/home/user/.ssh/id_rsa"})
        assert SensitivityCategory.PII_READ in result

    def test_read_file_of_non_sensitive_path_is_not_pii_read(self) -> None:
        result = sensitivity("read_file", {"path": "/tmp/scratch.txt"})
        assert SensitivityCategory.PII_READ not in result

    def test_read_tool_with_pii_placeholder_in_args_is_pii_read(self) -> None:
        result = sensitivity("web_search", {"query": "contact <PII:email>"})
        assert SensitivityCategory.PII_READ in result

    def test_read_tool_with_nested_pii_placeholder_is_pii_read(self) -> None:
        result = sensitivity(
            "search_files", {"filters": {"terms": ["ok", "<PII:ssn>"]}}
        )
        assert SensitivityCategory.PII_READ in result

    def test_read_tool_without_pii_or_sensitive_path_is_not_pii_read(self) -> None:
        result = sensitivity("web_search", {"query": "weather forecast"})
        assert SensitivityCategory.PII_READ not in result

    def test_write_tool_is_never_pii_read_even_with_placeholder(self) -> None:
        """PII_READ is scoped to READ tools only — a WRITE tool carrying a
        PII placeholder in its args is a DIFFERENT concern (data egress),
        not covered by this category."""
        result = sensitivity("write_file", {"content": "<PII:email>"})
        assert SensitivityCategory.PII_READ not in result

    def test_write_tool_sensitive_path_is_not_pii_read(self) -> None:
        result = sensitivity("write_file", {"path": "/home/user/.ssh/id_rsa"})
        assert SensitivityCategory.PII_READ not in result

    def test_unknown_tool_is_never_pii_read(self) -> None:
        result = sensitivity("some_unregistered_tool", {"path": "/home/user/.ssh/x"})
        assert SensitivityCategory.PII_READ not in result


# ---------------------------------------------------------------------------
# NEW_EGRESS
# ---------------------------------------------------------------------------


class TestNewEgress:
    def test_browser_navigate_to_ungranted_domain_is_new_egress(self) -> None:
        result = sensitivity(
            "browser_navigate", {"url": "https://evil.example.com/path"}
        )
        assert SensitivityCategory.NEW_EGRESS in result

    def test_browser_navigate_to_allowlisted_domain_is_not_new_egress(self) -> None:
        result = sensitivity(
            "browser_navigate",
            {"url": "https://good.example.com/path"},
            egress_allowlist=frozenset({"good.example.com"}),
        )
        assert SensitivityCategory.NEW_EGRESS not in result

    def test_web_extract_bare_domain_arg_is_new_egress(self) -> None:
        result = sensitivity("web_extract", {"domain": "unknown-host.test"})
        assert SensitivityCategory.NEW_EGRESS in result

    def test_non_egress_capable_tool_is_never_new_egress(self) -> None:
        """read_file happens to have no url/domain/host — but even if it did,
        it is not in the curated egress-capable set."""
        result = sensitivity("read_file", {"url": "https://evil.example.com"})
        assert SensitivityCategory.NEW_EGRESS not in result

    def test_egress_capable_tool_without_a_parseable_domain_is_not_new_egress(
        self,
    ) -> None:
        result = sensitivity("browser_navigate", {"selector": "#submit"})
        assert SensitivityCategory.NEW_EGRESS not in result


# ---------------------------------------------------------------------------
# SPEND
# ---------------------------------------------------------------------------


class TestSpend:
    def test_curated_payment_tool_is_spend(self) -> None:
        result = sensitivity("STRIPE_CREATE_PAYMENT_LINK", {"amount": 1000})
        assert SensitivityCategory.SPEND in result

    def test_uncurated_tool_is_never_spend(self) -> None:
        result = sensitivity("STRIPE_LIST_CUSTOMERS", {})
        assert SensitivityCategory.SPEND not in result

    def test_spend_is_not_a_keyword_scan(self) -> None:
        """A tool whose ARGS merely mention 'payment' must not be classified
        SPEND — SPEND is a curated tool-name contract, never a word scan."""
        result = sensitivity("web_search", {"query": "how do I create a payment"})
        assert SensitivityCategory.SPEND not in result


# ---------------------------------------------------------------------------
# SPEND — safent-ads companion (024/T091)
#
# Hardcoded, tight unit coverage for the curated set. See
# test_ads_companion_sensitivity_drift.py for the exhaustive, catalog.py-
# driven sweep that fails loudly on drift (a new ads write tool landing
# without a matching classification here).
# ---------------------------------------------------------------------------


class TestAdsCompanionSpend:
    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp__safent-ads__propose_budget_change",
            "mcp__safent-ads__propose_pause",
            "mcp__safent-ads__propose_targeting_change",
            "mcp__safent-ads__propose_creative_publication",
            "mcp__safent-ads__withdraw_proposal",
            "mcp__safent-ads__apply_defensive_action",
        ],
    )
    def test_every_ads_write_tool_is_spend(self, tool_name: str) -> None:
        assert SensitivityCategory.SPEND in sensitivity(tool_name, {})

    @pytest.mark.parametrize(
        "tool_name",
        [
            "mcp__safent-ads__list_campaigns",
            "mcp__safent-ads__get_campaign",
            "mcp__safent-ads__get_portfolio_overview",
            "mcp__safent-ads__explain_signal",
            "mcp__safent-ads__get_kill_switch_status",
        ],
    )
    def test_ads_read_tools_are_never_spend(self, tool_name: str) -> None:
        assert SensitivityCategory.SPEND not in sensitivity(tool_name, {})

    def test_matching_is_by_full_qualified_name_not_bare_tool_name(self) -> None:
        """A bundle cannot relabel an unrelated companion's tool into SPEND
        just by naming it identically — only a call actually resolved
        against the safent-ads slug (McpCapabilityRegistry's own
        binding.tool_name, mcp__<slug>__<tool>) qualifies."""
        assert SensitivityCategory.SPEND not in sensitivity("apply_defensive_action", {})
        assert (
            SensitivityCategory.SPEND
            not in sensitivity("mcp__some-other-companion__apply_defensive_action", {})
        )


# ---------------------------------------------------------------------------
# Fail-soft
# ---------------------------------------------------------------------------


class TestFailSoft:
    def test_none_args_does_not_raise(self) -> None:
        assert sensitivity("read_file", None) == frozenset()  # type: ignore[arg-type]

    def test_non_string_path_value_does_not_raise(self) -> None:
        assert sensitivity("read_file", {"path": 12345}) == frozenset()

    def test_non_string_url_value_does_not_raise(self) -> None:
        assert sensitivity("browser_navigate", {"url": 12345}) == frozenset()

    def test_empty_url_value_does_not_raise(self) -> None:
        assert sensitivity("browser_navigate", {"url": ""}) == frozenset()

    def test_unclassified_tool_with_odd_args_does_not_raise(self) -> None:
        assert sensitivity("totally_unknown_tool", {"weird": object()}) == frozenset()

    def test_malformed_nested_args_does_not_raise(self) -> None:
        result = sensitivity("search_files", {"filters": ["a", 1, None, {"x": 2}]})
        assert result == frozenset()


# ---------------------------------------------------------------------------
# Combination
# ---------------------------------------------------------------------------


class TestCombination:
    def test_multiple_categories_can_apply_at_once(self) -> None:
        result = sensitivity(
            "STRIPE_CREATE_CHECKOUT_SESSION",
            {"url": "https://checkout.example.com", "note": "<PII:card>"},
        )
        assert SensitivityCategory.SPEND in result

    def test_normal_read_tool_call_yields_empty_set(self) -> None:
        result = sensitivity("read_file", {"path": "/tmp/notes.txt"})
        assert result == frozenset()
