"""`safent uninstall` scoped to THIS installation (028 T003, matrix 025
UPD-06 + the CLI-12 nuance).

Before this: `safent uninstall` was hard-coded to the DEFAULT agent
label/unit/CLI-binary paths regardless of $SAFENT_NAME, and unconditionally
removed the shared `safent-companions` network and the data volume. A
second, named instance on the same host (multi-instance testing, a second
tenant) uninstalling itself would tear down the FIRST instance's host
agent and the CLI both instances share, and would rm a network the first
instance's companion still needed.

Drives the REAL `safent` script (sh, not sourced) with only podman faked —
same discipline as tests/unit/ops/test_companion_provision.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SAFENT_CLI = _REPO_ROOT / "safent"

_FAKE_PODMAN = r"""#!/usr/bin/env bash
set -e
echo "$@" >> "$FAKE_PODMAN_LOG"
case "$1" in
  inspect)
    case "$*" in
      *"-f "*) echo "${FAKE_RUNNING:-true}"; exit 0 ;;
      *) [ "${FAKE_CONTAINER_EXISTS:-1}" = "1" ] && exit 0 || exit 1 ;;
    esac
    ;;
  rm) exit 0 ;;
  network)
    case "$2" in
      inspect) [ "${FAKE_NETWORK_PRESENT:-1}" = "1" ] && exit 0 || exit 1 ;;
      rm) exit 0 ;;
    esac
    exit 0
    ;;
  ps)
    for id in ${FAKE_NETWORK_ATTACHED_IDS:-}; do echo "$id"; done
    exit 0
    ;;
  volume)
    case "$2" in
      rm) exit 0 ;;
    esac
    exit 0
    ;;
  machine)
    case "$2" in
      rm) exit 0 ;;
    esac
    exit 0
    ;;
  compose) exit 0 ;;
esac
exit 0
"""


@pytest.fixture()
def fake_bin_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    podman = bin_dir / "podman"
    podman.write_text(_FAKE_PODMAN)
    podman.chmod(0o755)
    return bin_dir


def _seed_companion_state(state_home: Path, *, provisioned: bool) -> None:
    state_home.mkdir(parents=True, exist_ok=True)
    (state_home / "safent-seccomp.json").write_text("{}")
    if not provisioned:
        return
    companion_dir = state_home / "companions" / "ads"
    bin_dir = companion_dir / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "compose.yaml").write_text("services: {}\n")
    (companion_dir / "secrets").mkdir(exist_ok=True)
    (companion_dir / "secrets" / "api.env").write_text("ADS_MCP_TOKEN=x\n")
    (companion_dir / "bearer").write_text("x\n")
    (companion_dir / "image").write_text("safent-ads:test-fake")


def _run_uninstall(
    tmp_path: Path,
    fake_bin_dir: Path,
    *args: str,
    name: str = "safent",
    home: Path | None = None,
    state_home: Path | None = None,
    provisioned_companion: bool = True,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    home_dir = home if home is not None else tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    resolved_state_home = state_home if state_home is not None else tmp_path / "state-home"
    _seed_companion_state(resolved_state_home, provisioned=provisioned_companion)
    podman_log = tmp_path / f"podman-{name}.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin_dir}:{os.environ.get('PATH', '')}",
        "HOME": str(home_dir),
        "SAFENT_STATE_HOME": str(resolved_state_home),
        "SAFENT_NAME": name,
        "SAFENT_DATA_VOLUME": f"{name}-data",
        "FAKE_PODMAN_LOG": str(podman_log),
    }
    env.update(extra_env or {})
    result = subprocess.run(
        ["sh", str(_SAFENT_CLI), "uninstall", *args],
        env=env, capture_output=True, text=True, timeout=60,
    )
    return result, podman_log


class TestScopeValidation:
    def test_default_scope_is_accepted(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, _ = _run_uninstall(tmp_path, fake_bin_dir, "--scope", "this-install")
        assert result.returncode == 0, result.stderr

    def test_bare_uninstall_defaults_to_this_install_scope(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        result, _ = _run_uninstall(tmp_path, fake_bin_dir)
        assert result.returncode == 0, result.stderr

    def test_an_unsupported_scope_is_rejected(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, _ = _run_uninstall(tmp_path, fake_bin_dir, "--scope", "everything")
        assert result.returncode != 0
        assert "this-install" in result.stderr


class TestDefaultKeepsData:
    def test_container_and_companion_stack_are_removed(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        result, podman_log = _run_uninstall(tmp_path, fake_bin_dir)
        assert result.returncode == 0, result.stderr
        log_lines = podman_log.read_text().splitlines()
        assert any(ln.startswith("rm -f safent") for ln in log_lines)
        assert any(ln.startswith("compose ") for ln in log_lines)

    def test_data_volume_is_not_removed(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, podman_log = _run_uninstall(tmp_path, fake_bin_dir)
        assert result.returncode == 0, result.stderr
        log = podman_log.read_text()
        assert "volume rm safent-data" not in log

    def test_companion_volumes_are_not_removed(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, podman_log = _run_uninstall(tmp_path, fake_bin_dir)
        assert result.returncode == 0, result.stderr
        log = podman_log.read_text()
        assert "safent-ads-companion-db-data" not in log

    def test_state_home_survives(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        state_home = tmp_path / "state-home"
        result, _ = _run_uninstall(tmp_path, fake_bin_dir, state_home=state_home)
        assert result.returncode == 0, result.stderr
        assert (state_home / "companions" / "ads" / "secrets" / "api.env").exists()

    def test_output_says_what_was_kept(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, _ = _run_uninstall(tmp_path, fake_bin_dir)
        assert result.returncode == 0, result.stderr
        assert "Kept" in result.stdout


class TestPurgeReallyDeletesEverything:
    def test_data_volume_is_removed(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, podman_log = _run_uninstall(tmp_path, fake_bin_dir, "--purge")
        assert result.returncode == 0, result.stderr
        assert "volume rm safent-data" in podman_log.read_text()

    def test_all_three_companion_volumes_are_removed(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        """CLI-12 nuance (matrix 025): --purge used to leave these three
        volumes (including the campaign database) behind."""
        result, podman_log = _run_uninstall(tmp_path, fake_bin_dir, "--purge")
        assert result.returncode == 0, result.stderr
        log = podman_log.read_text()
        for vol in (
            "safent-ads-companion-db-data",
            "safent-ads-companion-broker-sock",
            "safent-ads-companion-credential-store",
        ):
            assert f"volume rm {vol}" in log

    def test_state_home_is_deleted(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        state_home = tmp_path / "state-home"
        result, _ = _run_uninstall(tmp_path, fake_bin_dir, "--purge", state_home=state_home)
        assert result.returncode == 0, result.stderr
        assert not state_home.exists()

    def test_output_confirms_the_purge(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, _ = _run_uninstall(tmp_path, fake_bin_dir, "--purge")
        assert result.returncode == 0, result.stderr
        assert "purged" in result.stdout.lower()


class TestCompanionNetworkOnlyIfUnused:
    def test_network_is_removed_when_nothing_else_is_attached(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        result, podman_log = _run_uninstall(
            tmp_path, fake_bin_dir, extra_env={"FAKE_NETWORK_ATTACHED_IDS": ""}
        )
        assert result.returncode == 0, result.stderr
        assert "network rm safent-companions" in podman_log.read_text()

    def test_network_survives_when_another_container_is_still_attached(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        result, podman_log = _run_uninstall(
            tmp_path, fake_bin_dir, extra_env={"FAKE_NETWORK_ATTACHED_IDS": "other-instance-id"}
        )
        assert result.returncode == 0, result.stderr
        assert "network rm safent-companions" not in podman_log.read_text()
        assert "still in use by another instance" in result.stderr


class TestScopedToThisInstallationOnly:
    """The exact UPD-06 regression: a second, NAMED instance's uninstall
    must never touch the default instance's host agent or the shared CLI
    binary — both are HOME-global, and multiple instances share one HOME
    on the matrix's own multi-instance test host."""

    def test_named_instance_uses_its_own_suffixed_systemd_unit(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        home = tmp_path / "shared-home"
        default_unit = home / ".config" / "systemd" / "user" / "safent-agent.service"
        default_unit.parent.mkdir(parents=True)
        default_unit.write_text("[Unit]\n# pre-existing default-instance agent\n")

        named_unit = home / ".config" / "systemd" / "user" / "safent-agent-matrix-1.service"
        named_unit.write_text("[Unit]\n# this instance's own agent\n")

        result, _ = _run_uninstall(
            tmp_path, fake_bin_dir, name="matrix-1", home=home,
            state_home=tmp_path / "matrix-1-state",
        )

        assert result.returncode == 0, result.stderr
        assert default_unit.exists(), "uninstalling a NAMED instance deleted the DEFAULT instance's agent"
        assert not named_unit.exists()

    def test_named_instance_never_removes_the_shared_cli_binary(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        home = tmp_path / "shared-home"
        cli_link = home / ".local" / "bin" / "safent"
        cli_link.parent.mkdir(parents=True)
        cli_link.write_text("#!/bin/sh\n# stand-in for the shared installed CLI\n")

        result, _ = _run_uninstall(
            tmp_path, fake_bin_dir, name="matrix-1", home=home,
            state_home=tmp_path / "matrix-1-state",
        )

        assert result.returncode == 0, result.stderr
        assert cli_link.exists(), "uninstalling a NAMED instance deleted the shared CLI binary"

    def test_default_instance_still_removes_its_own_agent_and_cli(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        home = tmp_path / "home"
        unit = home / ".config" / "systemd" / "user" / "safent-agent.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("[Unit]\n")
        cli_link = home / ".local" / "bin" / "safent"
        cli_link.parent.mkdir(parents=True)
        cli_link.write_text("#!/bin/sh\n")

        result, _ = _run_uninstall(tmp_path, fake_bin_dir, name="safent", home=home)

        assert result.returncode == 0, result.stderr
        assert not unit.exists()
        assert not cli_link.exists()

    def test_container_is_scoped_by_name(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, podman_log = _run_uninstall(tmp_path, fake_bin_dir, name="matriz-final-2")
        assert result.returncode == 0, result.stderr
        log = podman_log.read_text()
        assert "rm -f matriz-final-2" in log
        assert "rm -f safent\n" not in log

    def test_separate_state_homes_never_cross_contaminate(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        """Uninstalling instance A must never delete instance B's state,
        even when both live under the same HOME (matrix 025's own
        multi-instance test posture)."""
        home = tmp_path / "shared-home"
        state_a = tmp_path / "state-a"
        state_b = tmp_path / "state-b"
        _seed_companion_state(state_b, provisioned=True)  # instance-b already exists

        result, _ = _run_uninstall(
            tmp_path, fake_bin_dir, "--purge", name="instance-a", home=home, state_home=state_a
        )
        assert result.returncode == 0, result.stderr
        assert not state_a.exists()

        # instance-b was never touched by instance-a's uninstall.
        assert (state_b / "companions" / "ads" / "secrets" / "api.env").exists()


class TestMachineRemovalIsANoOpOffDarwin:
    """`_uninstall_machine_if_ours` is a macOS-only concern (podman
    machines do not exist on Linux) — this only proves it never calls
    podman at all on this platform, never a full exercise of the
    adopted/created distinction (untestable without a Darwin host)."""

    def test_no_machine_command_is_issued(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        result, podman_log = _run_uninstall(tmp_path, fake_bin_dir)
        assert result.returncode == 0, result.stderr
        # Line-startswith, not a bare substring: the tmp_path itself
        # (embedded in logged compose.yaml paths) contains this test's own
        # name, "test_no_machine_command...", a false-positive substring
        # match a bare `"machine" in log` would trip on.
        log_lines = podman_log.read_text().splitlines()
        assert not any(ln.startswith("machine ") for ln in log_lines), log_lines
