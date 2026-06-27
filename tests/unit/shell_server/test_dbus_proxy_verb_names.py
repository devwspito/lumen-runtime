"""Guard against the PascalCase↔snake_case proxy-verb-name bug class.

DbusRuntimeProxy resolves a verb via getattr(iface, f"call_{member}") and
dbus-fast exposes interface methods as call_<snake_case>. So every member string
passed to proxy.call_dict/list/bool/mutator MUST be snake_case — a PascalCase
string (e.g. "GetMemoryEntry") silently fails closed with AgentUnavailable → the
REST handler returns 503/empty and the UI shows nothing ("dice OK pero roto").

Three real bugs of this class shipped (memory get/forget, scheduled-task get);
this test fails the build if any reappears.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SHELL_SERVER = Path(__file__).resolve().parents[3] / "src" / "hermes" / "shell_server"
_CALL_RE = re.compile(r"""\.call_(?:dict|list|bool|mutator)\(\s*["']([^"']+)["']""")


def test_all_proxy_verb_strings_are_snake_case() -> None:
    offenders: list[str] = []
    for py in _SHELL_SERVER.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            for member in _CALL_RE.findall(line):
                # snake_case = lowercase + digits + underscores only. A single
                # uppercase letter means a PascalCase D-Bus method name leaked in.
                if member != member.lower() or not re.fullmatch(r"[a-z0-9_]+", member):
                    offenders.append(f"{py.relative_to(_SHELL_SERVER)}:{i} → {member!r}")
    assert not offenders, (
        "proxy.call_* must use snake_case verb names (dbus-fast naming); "
        "PascalCase fails closed → 503/empty UI. Offenders:\n  " + "\n  ".join(offenders)
    )
