"""Every `forward` base chain in the container netns must let the companion through (024).

Regression for the rootful end-to-end blocker: `hermes_browser_egress` and
`hermes_mcp_egress` BOTH register a `forward` base chain on the SAME hook at
the SAME priority inside the container's network namespace. nftables evaluates
every registered base chain, and a `drop` is TERMINAL for the packet — not just
for its own chain. So the browser table's anti-pivot `ip daddr { 10.0.0.0/8, ...
} drop` was killing the MCP -> companion flow (10.201.0.0/24 lives inside
10.0.0.0/8) that `hermes_mcp_egress` had already accepted: 6 packets in, 0 out
on eth0, and the drop logged only to the kernel ring buffer the container cannot
read, which is why it looked like a silent black hole.

Invariant pinned here: a flow only survives if NO forward chain drops it, so
EVERY forward chain that carries a terminal RFC1918 drop must first `include`
the companion accept fragment. The included rule is pinned to `ip saddr
10.200.1.2` (the MCP netns) by companion_nft.py, so the browser gains nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hermes.shell_server.companion_nft import FORWARD_FRAGMENT_FILENAME

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NETNS_DIR = _REPO_ROOT / "ops/agents-os-edition/netns"

# Both host-side rulesets loaded into the container netns by
# hermes-browser-netns.service; both carry a `forward` hook chain.
_HOST_RULESETS = ("browser-host.nft", "mcp-host.nft")

_COMPANION_FWD_INCLUDE = 'include "/run/hermes/nft/companion-*-fwd.nft"'
_RFC1918_DROP = re.compile(r"ip daddr \{[^}]*10\.0\.0\.0/8[^}]*\}")


def _forward_chain(ruleset: str) -> str:
    body = ruleset.split("chain forward", 1)
    assert len(body) == 2, "ruleset has no `forward` chain"
    # Up to the closing brace of the chain (4-space-indented `}` at column 4).
    return body[1].split("\n    }", 1)[0]


@pytest.mark.parametrize("filename", _HOST_RULESETS)
class TestForwardChainsAgreeOnTheCompanion:
    def test_forward_chain_includes_the_companion_fragment(self, filename: str) -> None:
        chain = _forward_chain((_NETNS_DIR / filename).read_text(encoding="utf-8"))
        assert _COMPANION_FWD_INCLUDE in chain, (
            f"{filename}'s forward chain does not include the companion accept "
            "fragment. Both forward base chains share the hook, and a drop in "
            "either one is terminal -> the companion is unreachable from the MCP."
        )

    def test_companion_include_precedes_the_terminal_rfc1918_drop(self, filename: str) -> None:
        chain = _forward_chain((_NETNS_DIR / filename).read_text(encoding="utf-8"))
        drop = _RFC1918_DROP.search(chain)
        assert drop is not None, f"{filename}: no RFC1918 anti-pivot drop found"
        assert chain.index(_COMPANION_FWD_INCLUDE) < drop.start(), (
            f"{filename}: the companion include sits AFTER the RFC1918 drop; "
            "10.201.0.0/24 is inside 10.0.0.0/8 and a drop is terminal, so the "
            "accept would never be reached."
        )

    def test_include_is_a_wildcard_glob_so_a_missing_fragment_is_not_a_boot_error(
        self, filename: str
    ) -> None:
        chain = _forward_chain((_NETNS_DIR / filename).read_text(encoding="utf-8"))
        assert FORWARD_FRAGMENT_FILENAME.replace("ads", "*") in chain


def test_browser_forward_chain_still_drops_rfc1918_for_the_browser_itself() -> None:
    """The include must not have widened the browser's own anti-pivot."""
    chain = _forward_chain((_NETNS_DIR / "browser-host.nft").read_text(encoding="utf-8"))
    assert _RFC1918_DROP.search(chain) is not None
    assert 'log prefix "hbr FWD DROP: "' in chain
    # No blanket accept for the companion subnet ever appears as a RULE
    # (the `#` comment explaining why the include must come first does name it).
    rules = "\n".join(ln for ln in chain.splitlines() if not ln.lstrip().startswith("#"))
    assert "10.201.0.0/24" not in rules
