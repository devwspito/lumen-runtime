"""`safent backup` / `safent restore` — argument handling and refusal paths
(item #3, spec 025 matriz — backup/restore was entirely missing).

Runs the REAL `safent` CLI (POSIX sh) as a subprocess, with only `podman`
faked (a logging shim on PATH — see _FAKE_PODMAN); `tar`, the checksum tool,
`date`, `mktemp` are the real host tools, exactly like a real run. `HOME` is
redirected to an isolated tmp dir so nothing ever touches the real
`~/.safent`.

Covers:
  - backup refuses when there is no data volume to back up (nothing to
    stop/archive/restart).
  - a successful backup produces a 0600 archive containing manifest.json +
    data-volume.tar + state.tar, with sha256 entries that match the actual
    file contents.
  - backup only restarts Safent if it was running before (no surprise
    start on an already-stopped instance).
  - restore's usage error (no archive argument) and missing-file error.
  - restore REFUSES to overwrite an existing data volume without --force —
    the critical safety path — and does so WITHOUT calling `podman volume
    rm`/`import` (verified via the podman invocation log, not just the
    process exit code).
  - restore proceeds and imports the volume when --force is given.
  - restore refuses a tampered archive (sha256 mismatch) without ever
    calling `podman volume rm`/`import`.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SAFENT_CLI = _REPO_ROOT / "safent"

_FAKE_PODMAN = """#!/usr/bin/env bash
set -e
echo "$@" >> "$FAKE_PODMAN_LOG"

case "$1" in
  inspect)
    if [ "$2" = "-f" ]; then
      [ "$FAKE_CONTAINER_EXISTS" = "true" ] || exit 1
      echo "$FAKE_CONTAINER_RUNNING"
      exit 0
    fi
    [ "$FAKE_CONTAINER_EXISTS" = "true" ] && exit 0 || exit 1
    ;;
  volume)
    case "$2" in
      exists)
        [ "$FAKE_VOLUME_EXISTS" = "true" ] && exit 0 || exit 1
        ;;
      export)
        out=""
        shift 3
        while [ $# -gt 0 ]; do
          case "$1" in
            -o) out="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        printf 'FAKE-VOLUME-DATA' > "$out"
        exit 0
        ;;
      import|create|rm)
        exit 0
        ;;
      *)
        exit 0
        ;;
    esac
    ;;
  stop|start|pull|run)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""


@pytest.fixture()
def fake_bin_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    podman = bin_dir / "podman"
    podman.write_text(_FAKE_PODMAN)
    podman.chmod(0o755)
    return bin_dir


def _run_safent(
    *args: str,
    fake_bin_dir: Path,
    home_dir: Path,
    podman_log: Path,
    container_exists: bool = True,
    container_running: bool = False,
    volume_exists: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    home_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin_dir}:{env.get('PATH', '')}"
    env["HOME"] = str(home_dir)
    env["SAFENT_NAME"] = "safent-test"
    env["SAFENT_DATA_VOLUME"] = "safent-test-data"
    env["FAKE_PODMAN_LOG"] = str(podman_log)
    env["FAKE_CONTAINER_EXISTS"] = "true" if container_exists else "false"
    env["FAKE_CONTAINER_RUNNING"] = "true" if container_running else "false"
    env["FAKE_VOLUME_EXISTS"] = "true" if volume_exists else "false"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["sh", str(_SAFENT_CLI), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _podman_calls(podman_log: Path) -> list[str]:
    if not podman_log.exists():
        return []
    return [line for line in podman_log.read_text().splitlines() if line]


class TestBackupRefusesWithoutAVolume:
    def test_no_volume_to_back_up_is_a_clean_error_not_a_crash(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        result = _run_safent(
            "backup",
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=tmp_path / "podman.log",
            volume_exists=False,
        )
        assert result.returncode != 0
        assert "nothing to do" in result.stdout.lower() or "nothing to do" in result.stderr.lower()


class TestSuccessfulBackup:
    def test_produces_a_0600_archive_with_manifest_and_matching_checksums(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        out_dir = tmp_path / "backups"
        result = _run_safent(
            "backup", str(out_dir),
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=tmp_path / "podman.log",
            container_exists=True,
            container_running=True,
            volume_exists=True,
        )
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

        archives = list(out_dir.glob("safent-backup-*.tar.gz"))
        assert len(archives) == 1
        archive = archives[0]
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with tarfile.open(archive) as tf:
            tf.extractall(extract_dir, filter="data")

        manifest = json.loads((extract_dir / "manifest.json").read_text())
        for member in ("data-volume.tar", "state.tar"):
            actual = hashlib.sha256((extract_dir / member).read_bytes()).hexdigest()
            assert manifest["sha256"][member] == actual, member
        assert manifest["data_volume"] == "safent-test-data"

    def test_restarts_only_if_it_was_running_before(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        result = _run_safent(
            "backup",
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=podman_log,
            container_exists=True,
            container_running=False,  # already stopped BEFORE the backup
            volume_exists=True,
        )
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        calls = _podman_calls(podman_log)
        assert not any(c.startswith("start ") for c in calls), calls


class TestRestoreArgumentHandling:
    def test_no_archive_argument_is_a_usage_error(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        result = _run_safent(
            "restore",
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=tmp_path / "podman.log",
        )
        assert result.returncode != 0
        assert "usage" in (result.stdout + result.stderr).lower()

    def test_missing_archive_file_errors_before_touching_podman(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        result = _run_safent(
            "restore", str(tmp_path / "does-not-exist.tar.gz"),
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=podman_log,
        )
        assert result.returncode != 0
        assert "not found" in (result.stdout + result.stderr).lower()
        assert _podman_calls(podman_log) == []


def _make_backup(
    tmp_path: Path, fake_bin_dir: Path, out_dir: Path
) -> Path:
    result = _run_safent(
        "backup", str(out_dir),
        fake_bin_dir=fake_bin_dir,
        home_dir=tmp_path / "home-for-backup",
        podman_log=tmp_path / "podman-backup.log",
        container_exists=True,
        container_running=True,
        volume_exists=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    (archive,) = out_dir.glob("safent-backup-*.tar.gz")
    return archive


class TestRestoreRefusesToOverwriteWithoutForce:
    def test_refuses_when_volume_exists_and_no_force(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        archive = _make_backup(tmp_path, fake_bin_dir, tmp_path / "backups")
        podman_log = tmp_path / "podman-restore.log"

        result = _run_safent(
            "restore", str(archive),
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home-for-restore",
            podman_log=podman_log,
            container_exists=True,
            container_running=True,
            volume_exists=True,  # the volume we'd be about to clobber
        )

        assert result.returncode != 0
        assert "force" in (result.stdout + result.stderr).lower()
        calls = _podman_calls(podman_log)
        # the refusal must be BEFORE any destructive step — no rm/import ever issued
        assert not any(c.startswith("volume rm") for c in calls), calls
        assert not any(c.startswith("volume import") for c in calls), calls
        # ...and no stop either: refuse fast, touch nothing
        assert not any(c.startswith("stop") for c in calls), calls

    def test_proceeds_and_imports_the_volume_with_force(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        archive = _make_backup(tmp_path, fake_bin_dir, tmp_path / "backups")
        podman_log = tmp_path / "podman-restore.log"

        # container_running=False: the fake reports a STATIC state (it does
        # not model stop/start transitions), so "already stopped" is what
        # exercises cmd_start's `podman start` branch at the end — matching
        # the real-world case (restore always stops first regardless).
        result = _run_safent(
            "restore", str(archive), "--force",
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home-for-restore",
            podman_log=podman_log,
            container_exists=True,
            container_running=False,
            volume_exists=True,
        )

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        calls = _podman_calls(podman_log)
        assert any(c.startswith("volume rm") for c in calls), calls
        assert any(c.startswith("volume create") for c in calls), calls
        assert any(c.startswith("volume import") for c in calls), calls
        assert any(c.startswith("start ") for c in calls), calls  # restarted at the end

    def test_force_before_the_archive_path_is_also_accepted(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        """`--force` and the archive path may arrive in either order."""
        archive = _make_backup(tmp_path, fake_bin_dir, tmp_path / "backups")
        result = _run_safent(
            "restore", "--force", str(archive),
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home-for-restore",
            podman_log=tmp_path / "podman-restore.log",
            container_exists=True,
            container_running=True,
            volume_exists=True,
        )
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


class TestRestoreRefusesATamperedArchive:
    def test_sha256_mismatch_refuses_without_touching_the_volume(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        archive = _make_backup(tmp_path, fake_bin_dir, tmp_path / "backups")

        # Corrupt data-volume.tar in place, keeping the (now stale) manifest checksum.
        extract_dir = tmp_path / "tamper"
        extract_dir.mkdir()
        with tarfile.open(archive) as tf:
            tf.extractall(extract_dir, filter="data")
        (extract_dir / "data-volume.tar").write_bytes(b"TAMPERED")
        tampered = tmp_path / "tampered.tar.gz"
        with tarfile.open(tampered, "w:gz") as tf:
            for name in ("manifest.json", "data-volume.tar", "state.tar"):
                tf.add(extract_dir / name, arcname=name)

        podman_log = tmp_path / "podman-restore.log"
        result = _run_safent(
            "restore", str(tampered), "--force",
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home-for-restore",
            podman_log=podman_log,
            container_exists=True,
            container_running=True,
            volume_exists=False,
        )

        assert result.returncode != 0
        assert "checksum" in (result.stdout + result.stderr).lower()
        calls = _podman_calls(podman_log)
        assert not any(c.startswith("volume rm") for c in calls), calls
        assert not any(c.startswith("volume import") for c in calls), calls
