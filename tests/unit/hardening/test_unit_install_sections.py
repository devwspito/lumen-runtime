"""`WantedBy=` only means something in `[Install]` (024).

hermes-browser-netns.service and hermes-companion-egress.service both carried
`WantedBy=hermes-workspace.target` in `[Unit]`, where systemd does not know the
key: it is parsed, warned about ("Unknown key name 'WantedBy' in section
'Unit', ignoring") and DISCARDED. Both units happened to also declare it in
`[Install]`, so enablement worked by accident — the stray line was a trap that
would have made the next unit copied from them silently never start.

Pinned generically so no unit in this repo can reintroduce an install
directive outside `[Install]`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_UNIT_DIR = Path(__file__).resolve().parents[3] / "ops/agents-os-edition/systemd"
_INSTALL_KEYS = re.compile(r"^\s*(WantedBy|RequiredBy|UpheldBy|Also|Alias|DefaultInstance)\s*=")
_SECTION = re.compile(r"^\[([A-Za-z]+)\]\s*$")


def _install_keys_outside_install(text: str) -> list[str]:
    section, offenders = None, []
    for line in text.splitlines():
        header = _SECTION.match(line)
        if header:
            section = header.group(1)
            continue
        if _INSTALL_KEYS.match(line) and section != "Install":
            offenders.append(f"[{section}] {line.strip()}")
    return offenders


def _units() -> list[Path]:
    return sorted(p for p in _UNIT_DIR.iterdir() if p.suffix in {
        ".service", ".target", ".path", ".timer", ".socket", ".slice"
    })


@pytest.mark.parametrize("unit", _units(), ids=lambda p: p.name)
def test_install_directives_live_only_in_the_install_section(unit: Path) -> None:
    offenders = _install_keys_outside_install(unit.read_text(encoding="utf-8"))
    assert not offenders, (
        f"{unit.name}: systemd ignores these outside [Install] -> {offenders}"
    )


@pytest.mark.parametrize(
    "name",
    ["hermes-browser-netns.service", "hermes-companion-egress.service"],
)
def test_the_two_regressed_units_still_declare_their_install_target(name: str) -> None:
    text = (_UNIT_DIR / name).read_text(encoding="utf-8")
    install = text.split("[Install]", 1)
    assert len(install) == 2, f"{name} lost its [Install] section"
    assert "WantedBy=hermes-workspace.target" in install[1]
