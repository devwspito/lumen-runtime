"""Tests for the denylist + blocklist extensions to the egress policy engine.

Covers:
  - OPEN_LOGGED allows a normal domain (no blocklist/denylist hit).
  - OPEN_LOGGED denies a domain in the owner's denylist.
  - OPEN_LOGGED denies a domain in the system blocklist.
  - OPEN_LOGGED denies a subdomain of a blocked domain (subdomain matching).
  - OPEN_LOGGED denies a subdomain of a denied domain (subdomain matching).
  - DEFAULT_DENY is unaffected by blocklist and denylist (whitelist only).
  - load_blocklist populates the engine's internal blocklist.
  - blocklist_size reflects loaded entries.
  - Pinned MCP policy is never affected by browser denylist/blocklist operations
    (the blocklist applies only in OPEN_LOGGED; the MCP pin is DEFAULT_DENY).
  - control_command parses the optional ``deny`` field.
  - control_command without ``deny`` → empty denylist (backward-compat).
"""

from __future__ import annotations

import json

import pytest

from hermes.egress_proxy.application.control_command import (
    ControlCommandError,
    parse_control_command,
)
from hermes.egress_proxy.domain.policy import (
    EgressMode,
    EgressPolicyEngine,
    SessionPolicy,
)

pytestmark = pytest.mark.unit

_SESSION = "10.200.0.2"
_MCP_CLIENT_IP = "10.200.1.2"
_MALICIOUS = "malicious.example"
_DENYLIST_DOMAIN = "blocked-by-owner.example"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine_open_logged(
    blocklist: set[str] | None = None,
    denylist: frozenset[str] = frozenset(),
) -> EgressPolicyEngine:
    policy = SessionPolicy(
        session_id="__global__",
        mode=EgressMode.OPEN_LOGGED,
        domains_denylist=denylist,
    )
    engine = EgressPolicyEngine(global_policy=policy)
    if blocklist is not None:
        engine.load_blocklist(blocklist)
    return engine


def _engine_deny(whitelist: frozenset[str] = frozenset()) -> EgressPolicyEngine:
    policy = SessionPolicy(
        session_id="__global__",
        mode=EgressMode.DEFAULT_DENY,
        domains_whitelist=whitelist,
    )
    return EgressPolicyEngine(global_policy=policy)


# ---------------------------------------------------------------------------
# OPEN_LOGGED: happy path
# ---------------------------------------------------------------------------


class TestOpenLoggedAllows:
    def test_allows_normal_domain(self) -> None:
        engine = _engine_open_logged()
        decision = engine.evaluate(domain="example.com", session_id=_SESSION)
        assert decision.allowed is True
        assert decision.mode == EgressMode.OPEN_LOGGED

    def test_reason_contains_allowed(self) -> None:
        engine = _engine_open_logged()
        decision = engine.evaluate(domain="example.com", session_id=_SESSION)
        assert "allowed" in decision.reason


# ---------------------------------------------------------------------------
# OPEN_LOGGED: blocklist enforcement
# ---------------------------------------------------------------------------


class TestOpenLoggedBlocklist:
    def test_denies_domain_in_blocklist(self) -> None:
        engine = _engine_open_logged(blocklist={_MALICIOUS})
        decision = engine.evaluate(domain=_MALICIOUS, session_id=_SESSION)
        assert decision.allowed is False
        assert "malicious-blocklist" in decision.reason

    def test_denies_subdomain_of_blocked_domain(self) -> None:
        engine = _engine_open_logged(blocklist={_MALICIOUS})
        decision = engine.evaluate(domain=f"sub.{_MALICIOUS}", session_id=_SESSION)
        assert decision.allowed is False

    def test_denies_deep_subdomain_of_blocked_domain(self) -> None:
        engine = _engine_open_logged(blocklist={_MALICIOUS})
        decision = engine.evaluate(domain=f"a.b.{_MALICIOUS}", session_id=_SESSION)
        assert decision.allowed is False

    def test_does_not_deny_suffix_only_mismatch(self) -> None:
        """``notmalicious.example`` must NOT be blocked when only ``malicious.example`` is."""
        engine = _engine_open_logged(blocklist={_MALICIOUS})
        decision = engine.evaluate(domain="notmalicious.example", session_id=_SESSION)
        assert decision.allowed is True

    def test_empty_blocklist_allows_all(self) -> None:
        engine = _engine_open_logged(blocklist=set())
        decision = engine.evaluate(domain=_MALICIOUS, session_id=_SESSION)
        assert decision.allowed is True

    def test_load_blocklist_replaces_previous(self) -> None:
        engine = _engine_open_logged()
        engine.load_blocklist({"old.evil.example"})
        engine.load_blocklist({"new.evil.example"})
        assert engine.evaluate(domain="old.evil.example", session_id=_SESSION).allowed is True
        assert engine.evaluate(domain="new.evil.example", session_id=_SESSION).allowed is False

    def test_blocklist_size_reflects_entries(self) -> None:
        engine = _engine_open_logged()
        engine.load_blocklist({"a.example", "b.example", "c.example"})
        assert engine.blocklist_size == 3

    def test_blocklist_normalizes_case(self) -> None:
        engine = _engine_open_logged()
        engine.load_blocklist({"EVIL.EXAMPLE"})
        assert engine.evaluate(domain="evil.example", session_id=_SESSION).allowed is False


# ---------------------------------------------------------------------------
# OPEN_LOGGED: denylist enforcement
# ---------------------------------------------------------------------------


class TestOpenLoggedDenylist:
    def test_denies_domain_in_denylist(self) -> None:
        engine = _engine_open_logged(denylist=frozenset({_DENYLIST_DOMAIN}))
        decision = engine.evaluate(domain=_DENYLIST_DOMAIN, session_id=_SESSION)
        assert decision.allowed is False
        assert "owner-denylist" in decision.reason

    def test_denies_subdomain_of_denied_domain(self) -> None:
        engine = _engine_open_logged(denylist=frozenset({_DENYLIST_DOMAIN}))
        decision = engine.evaluate(domain=f"api.{_DENYLIST_DOMAIN}", session_id=_SESSION)
        assert decision.allowed is False

    def test_does_not_deny_suffix_only_mismatch_denylist(self) -> None:
        engine = _engine_open_logged(denylist=frozenset({_DENYLIST_DOMAIN}))
        decision = engine.evaluate(domain=f"not{_DENYLIST_DOMAIN}", session_id=_SESSION)
        assert decision.allowed is True

    def test_blocklist_takes_precedence_over_denylist(self) -> None:
        """Both blocklist and denylist deny — blocklist reason wins (checked first)."""
        engine = _engine_open_logged(
            blocklist={_MALICIOUS},
            denylist=frozenset({_MALICIOUS}),
        )
        decision = engine.evaluate(domain=_MALICIOUS, session_id=_SESSION)
        assert decision.allowed is False
        assert "malicious-blocklist" in decision.reason

    def test_denylist_via_pushed_session_policy(self) -> None:
        """Denylist pushed via push_policy / replace_global (control socket path)."""
        engine = _engine_open_logged(blocklist=set())
        engine.replace_global(SessionPolicy(
            session_id="__global__",
            mode=EgressMode.OPEN_LOGGED,
            domains_denylist=frozenset({_DENYLIST_DOMAIN}),
        ))
        assert engine.evaluate(domain=_DENYLIST_DOMAIN, session_id=_SESSION).allowed is False
        assert engine.evaluate(domain="safe.example.com", session_id=_SESSION).allowed is True


# ---------------------------------------------------------------------------
# DEFAULT_DENY: unaffected by blocklist / denylist
# ---------------------------------------------------------------------------


class TestDefaultDenyUnaffected:
    def test_whitelist_domain_allowed_even_in_blocklist(self) -> None:
        """A domain that happens to be in the blocklist is still allowed in DEFAULT_DENY
        if it is whitelisted — the blocklist only blocks in OPEN_LOGGED."""
        engine = _engine_deny(whitelist=frozenset({"whitelisted.example"}))
        engine.load_blocklist({"whitelisted.example"})
        decision = engine.evaluate(domain="whitelisted.example", session_id=_SESSION)
        assert decision.allowed is True

    def test_non_whitelisted_domain_denied_regardless_of_blocklist(self) -> None:
        engine = _engine_deny(whitelist=frozenset({"good.example"}))
        engine.load_blocklist({"bad.example"})
        assert engine.evaluate(domain="bad.example", session_id=_SESSION).allowed is False
        assert engine.evaluate(domain="also-bad.example", session_id=_SESSION).allowed is False


# ---------------------------------------------------------------------------
# Pinned MCP policy invariants
# ---------------------------------------------------------------------------


class TestPinnedMcpNotAffected:
    def test_mcp_pin_is_default_deny_regardless_of_global_blocklist(self) -> None:
        """The MCP pinned policy is DEFAULT_DENY. The blocklist only applies in
        OPEN_LOGGED — so the MCP's policy is evaluated as DEFAULT_DENY, not as
        OPEN_LOGGED, and the blocklist is irrelevant to it."""
        engine = EgressPolicyEngine(
            global_policy=SessionPolicy(
                session_id="__global__",
                mode=EgressMode.OPEN_LOGGED,
            )
        )
        engine.load_blocklist({"safe-for-mcp.example"})
        engine.pin_policy(
            client_id=_MCP_CLIENT_IP,
            policy=SessionPolicy(
                session_id="__mcp__",
                mode=EgressMode.DEFAULT_DENY,
                domains_whitelist=frozenset({"safe-for-mcp.example"}),
            ),
        )
        # MCP plane: DEFAULT_DENY + whitelist → whitelisted domain allowed.
        decision = engine.evaluate(domain="safe-for-mcp.example", session_id=_MCP_CLIENT_IP)
        assert decision.allowed is True
        assert decision.mode == EgressMode.DEFAULT_DENY

    def test_open_logged_browser_does_not_widen_mcp_pin(self) -> None:
        engine = EgressPolicyEngine(
            global_policy=SessionPolicy(
                session_id="__global__",
                mode=EgressMode.DEFAULT_DENY,
            )
        )
        engine.pin_policy(
            client_id=_MCP_CLIENT_IP,
            policy=SessionPolicy(
                session_id="__mcp__",
                mode=EgressMode.DEFAULT_DENY,
                domains_whitelist=frozenset(),
            ),
        )
        # Flip global to OPEN_LOGGED (browser teaching session).
        engine.replace_global(SessionPolicy(
            session_id="__global__",
            mode=EgressMode.OPEN_LOGGED,
        ))
        # MCP plane must remain DEFAULT_DENY — pin is immutable.
        mcp_decision = engine.evaluate(domain="evil.com", session_id=_MCP_CLIENT_IP)
        assert mcp_decision.allowed is False
        assert mcp_decision.mode == EgressMode.DEFAULT_DENY


# ---------------------------------------------------------------------------
# control_command.py: deny field parsing
# ---------------------------------------------------------------------------


class TestControlCommandDenyField:
    def test_deny_field_parsed(self) -> None:
        raw = json.dumps({
            "session_id": "s1",
            "mode": "open-logged",
            "deny": ["tracker.evil.example", "ads.evil.example"],
        })
        policy = parse_control_command(raw)
        assert "tracker.evil.example" in policy.domains_denylist
        assert "ads.evil.example" in policy.domains_denylist

    def test_deny_field_normalized_lowercase(self) -> None:
        raw = json.dumps({
            "session_id": "s2",
            "mode": "open-logged",
            "deny": ["TRACKER.EVIL.EXAMPLE"],
        })
        policy = parse_control_command(raw)
        assert "tracker.evil.example" in policy.domains_denylist

    def test_no_deny_field_gives_empty_denylist(self) -> None:
        raw = json.dumps({"session_id": "s3", "mode": "open-logged", "domains": []})
        policy = parse_control_command(raw)
        assert len(policy.domains_denylist) == 0

    def test_deny_not_list_raises(self) -> None:
        raw = json.dumps({
            "session_id": "s4",
            "mode": "open-logged",
            "deny": "evil.com",
        })
        with pytest.raises(ControlCommandError, match="lista"):
            parse_control_command(raw)

    def test_deny_and_domains_both_parsed(self) -> None:
        raw = json.dumps({
            "session_id": "s5",
            "mode": "default-deny",
            "domains": ["allowed.example"],
            "deny": ["blocked.example"],
        })
        policy = parse_control_command(raw)
        assert "allowed.example" in policy.domains_whitelist
        assert "blocked.example" in policy.domains_denylist

    def test_too_many_deny_domains_rejected(self) -> None:
        raw = json.dumps({
            "session_id": "s6",
            "mode": "open-logged",
            "deny": ["a.com"] * 5000,
        })
        with pytest.raises(ControlCommandError):
            parse_control_command(raw)
