"""run-safent.sh must relax AppArmor ONLY where AppArmor is what breaks PID1 (024).

Reproduced on rootful podman under an enforcing AppArmor (Ubuntu):
`--cap-add SYS_ADMIN` + `--systemd=always` + podman's `containers-default-*`
profile => systemd PID1 dies before its first log line, container exits 255.
`--security-opt apparmor=unconfined` is the only thing that fixes it.

Pinned here so the flag can never become unconditional: on macOS (and inside
the Fedora CoreOS VM behind `podman machine`) /sys/module/apparmor does not
exist, and that path must keep running with podman's default profile.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "ops/container/run-safent.sh"
).read_text(encoding="utf-8")

_ENABLED_PATH = "/sys/module/apparmor/parameters/enabled"


class TestAppArmorIsRelaxedOnlyWhenEnforcing:
    def test_detects_apparmor_through_the_kernel_module_parameter(self) -> None:
        assert _ENABLED_PATH in _SCRIPT

    def test_unconfined_is_set_only_inside_the_detection_branch(self) -> None:
        branch = re.search(
            r"if \[ \"\$\(cat " + re.escape(_ENABLED_PATH) + r".*?\n(.*?)\nfi\n",
            _SCRIPT,
            re.DOTALL,
        )
        assert branch is not None, "no AppArmor detection branch found"
        assert "apparmor=unconfined" in branch.group(1)

    def test_the_run_invocation_never_hardcodes_unconfined(self) -> None:
        run_line = _SCRIPT.split('exec "$RUNTIME" run -d', 1)
        assert len(run_line) == 2, "run invocation not found"
        assert "apparmor=unconfined" not in run_line[1], (
            "apparmor=unconfined is hardcoded on the run line — it would also "
            "apply on macOS/podman-machine, which must keep podman's default."
        )

    def test_run_invocation_expands_the_conditional_array(self) -> None:
        assert 'APPARMOR_RUN_ARGS[@]+"${APPARMOR_RUN_ARGS[@]}"' in _SCRIPT

    def test_array_defaults_to_empty(self) -> None:
        assert "APPARMOR_RUN_ARGS=()" in _SCRIPT

    def test_detection_never_fails_the_script_when_the_file_is_absent(self) -> None:
        """`set -euo pipefail` is on: a missing/unreadable file must not abort."""
        assert "2>/dev/null || true" in _SCRIPT.split(_ENABLED_PATH, 1)[1][:40]
