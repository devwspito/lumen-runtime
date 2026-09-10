"""`safent --porcelain` — the NDJSON wire protocol (T004, contracts/app-engine.md).

Runs the REAL `safent` CLI (POSIX sh) as a subprocess, with only `podman`
faked (a logging shim on PATH — same technique as
tests/unit/ops/test_safent_cli_backup_restore.py). `HOME` is redirected to an
isolated tmp dir so nothing ever touches the real `~/.safent`.

Covers the protocol invariants app-engine.md defines, not engine behavior:
  - stdout in porcelain mode is line-delimited JSON ONLY: every line parses,
    every line has a known `t`, every `stage` closes with exactly one `done`
    XOR one `failed` before the next stage opens (§2, §3).
  - the bootstrap ticket (`?k=...`) NEVER appears on stdout or stderr, only
    on the dedicated `--secret-fd` (§5).
  - a `failed` event's `code` maps to the documented closed vocabulary and
    the process exit code lands in the stable 10..39 domain range (§2/§3).
  - without --porcelain, the same verbs still work and print human text
    instead (§1: the flag "no altera el comportamiento").
  - `facts` is pure: it never calls a mutating podman verb (run/rm/pull/stop).
  - `SAFENT_PODMAN` wins over PATH resolution (§1 env table).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SAFENT_CLI = _REPO_ROOT / "safent"

_SECRET_TOKEN = "s3cr3t-token-do-not-leak"  # noqa: S105 - test fixture, not a real credential

_KNOWN_EVENT_TYPES = {"stage", "progress", "done", "failed", "facts", "ready"}

_FAKE_PODMAN = """#!/usr/bin/env bash
set -e
echo "$@" >> "$FAKE_PODMAN_LOG"

case "$1" in
  inspect)
    shift
    # BKP-01: the CLI now pins `inspect --type container` so a same-named
    # volume can never satisfy a container check — accept the flag pair here.
    if [ "$1" = "--type" ]; then shift 2; fi
    if [ "$1" = "-f" ]; then
      case "$2" in
        '{{.State.Running}}')
          [ "$FAKE_CONTAINER_EXISTS" = "true" ] || exit 1
          echo "$FAKE_CONTAINER_RUNNING"; exit 0 ;;
        '{{.Image}}')
          [ "$FAKE_CONTAINER_EXISTS" = "true" ] || exit 1
          echo "sha256:$FAKE_IMAGE_DIGEST"; exit 0 ;;
      esac
      exit 0
    fi
    [ "$FAKE_CONTAINER_EXISTS" = "true" ] && exit 0 || exit 1
    ;;
  image)
    [ "$2" = "exists" ] || exit 0
    [ "$FAKE_IMAGE_LOCAL" = "true" ] && exit 0 || exit 1
    ;;
  volume)
    case "$2" in
      exists) [ "$FAKE_VOLUME_EXISTS" = "true" ] && exit 0 || exit 1 ;;
      *) exit 0 ;;
    esac
    ;;
  port)
    echo "0.0.0.0:$FAKE_PORT"
    exit 0
    ;;
  exec)
    shift 2  # drop "exec" "$NAME"
    case "$1" in
      systemctl)
        [ "$FAKE_HEALTH_ACTIVE" = "true" ] && echo active || echo failed
        exit 0 ;;
      cat)
        [ "$FAKE_HEALTH_ACTIVE" = "true" ] && printf '%s' "$FAKE_SECRET"
        exit 0 ;;
      python3)
        printf '%s' "$FAKE_APP_VERSION"; exit 0 ;;
      *) exit 0 ;;
    esac
    ;;
  run)
    # _ensure_seccomp / _fetch_companion_file probe the image via
    # `run --rm --entrypoint cat <image> <path>` and need non-empty stdout
    # on success so they never fall through to a real network fetch.
    case " $* " in
      *" --entrypoint cat "*) printf '#!/bin/sh\\nexit 0\\n' ;;
    esac
    exit 0
    ;;
  pull)
    [ "${FAKE_PULL_FAILS:-false}" = "true" ] && exit 1
    exit 0
    ;;
  rm|start|stop)
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


def _base_env(
    *,
    fake_bin_dir: Path,
    home_dir: Path,
    podman_log: Path,
    container_exists: bool = True,
    container_running: bool = True,
    volume_exists: bool = True,
    health_active: bool = True,
    port: str = "17517",
    image_digest: str = "deadbeef",
    image_local: bool = True,
    app_version: str = "0.8.42",
    secret: str = _SECRET_TOKEN,
    pull_fails: bool = False,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
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
    env["FAKE_HEALTH_ACTIVE"] = "true" if health_active else "false"
    env["FAKE_PORT"] = port
    env["FAKE_IMAGE_DIGEST"] = image_digest
    env["FAKE_IMAGE_LOCAL"] = "true" if image_local else "false"
    env["FAKE_APP_VERSION"] = app_version
    env["FAKE_SECRET"] = secret
    env["FAKE_PULL_FAILS"] = "true" if pull_fails else "false"
    if extra_env:
        env.update(extra_env)
    return env


def _run_safent(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
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


def _parse_ndjson(stdout: str) -> list[dict]:
    lines = [line for line in stdout.splitlines() if line]
    events = []
    for line in lines:
        parsed = json.loads(line)  # raises if any line is not valid JSON
        assert parsed.get("t") in _KNOWN_EVENT_TYPES, parsed
        events.append(parsed)
    return events


def _assert_stage_closure_invariant(events: list[dict]) -> None:
    """Every `stage` closes with exactly one `done` XOR one `failed` before
    the next stage opens; `progress` only appears within its own open stage."""
    open_stage = None
    for ev in events:
        t = ev["t"]
        if t == "stage":
            assert open_stage is None, f"stage {ev['id']!r} opened while {open_stage!r} was still open"
            open_stage = ev["id"]
        elif t == "progress":
            assert ev["id"] == open_stage, ev
        elif t in ("done", "failed"):
            assert ev["id"] == open_stage, ev
            open_stage = None
    assert open_stage is None, f"stage {open_stage!r} never closed"


def _make_bundle(tmp_path: Path, entries: list[tuple[str, bytes, str]], podman_version: str = "6.1.1") -> Path:
    """A minimal <bundle>/engine/ layout: a real copy of `safent` alongside a
    runtime-bundle.json manifest and the binaries it describes."""
    engine_dir = tmp_path / "bundle" / "engine"
    engine_dir.mkdir(parents=True)
    (engine_dir / "safent").write_bytes(_SAFENT_CLI.read_bytes())
    (engine_dir / "safent").chmod(0o755)

    manifest_entries = []
    for rel_path, content, mode in entries:
        target = engine_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        manifest_entries.append(
            {"path": rel_path, "sha256": hashlib.sha256(content).hexdigest(), "mode": mode}
        )
    manifest = {"podman_version": podman_version, "entries": manifest_entries}
    (engine_dir / "runtime-bundle.json").write_text(json.dumps(manifest, indent=2))
    return engine_dir


class TestFactsIsPureObservation:
    def test_bare_facts_emits_one_line_of_parseable_json(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        result = _run_safent("facts", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        lines = [line for line in result.stdout.splitlines() if line]
        assert len(lines) == 1
        facts = json.loads(lines[0])
        assert facts["os"] == "linux"
        assert isinstance(facts["freeDiskBytes"], int)
        assert facts["engineContainer"]["running"] is True
        assert facts["engineContainer"]["imageDigest"] == "sha256:deadbeef"
        assert facts["dataVolume"] is True

    def test_porcelain_facts_is_wrapped_in_a_facts_event(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        result = _run_safent("facts", "--porcelain", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        events = _parse_ndjson(result.stdout)
        assert len(events) == 1
        assert events[0]["t"] == "facts"
        assert "facts" in events[0]

    def test_facts_never_calls_a_mutating_podman_verb(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        result = _run_safent("facts", "--porcelain", env=env)
        assert result.returncode == 0
        calls = _podman_calls(podman_log)
        for verb in ("run", "rm", "pull", "stop", "start"):
            assert not any(c.startswith(verb + " ") or c == verb for c in calls), calls

    def test_facts_reflects_absent_container_and_volume(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=podman_log,
            container_exists=False,
            volume_exists=False,
        )
        result = _run_safent("facts", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        facts = json.loads(result.stdout.strip())
        assert facts["engineContainer"]["exists"] is False
        assert facts["engineContainer"]["running"] is False
        assert facts["engineContainer"]["imageDigest"] is None
        assert facts["dataVolume"] is False

    def test_local_engine_image_digest_reflects_podman_image_exists_for_a_digest_pinned_image(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        """Regression test (app-desk-integration): without this field,
        reconcile.rs's images_gap() has no way to tell "already pulled" from
        "never pulled" independent of whether a container is running it yet
        — it would ask to pull forever, even against an already-converged
        engine. `localEngineImageDigest`/`localCompanionImageDigest` close
        that gap; `podman image exists` decides them."""
        podman_log = tmp_path / "podman.log"
        env = _base_env(
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=podman_log,
            image_local=True,
            extra_env={
                "SAFENT_IMAGE": "ghcr.io/devwspito/safent@sha256:engineexample",
                "SAFENT_ADS_IMAGE": "ghcr.io/devwspito/safent-ads@sha256:adsexample",
            },
        )
        result = _run_safent("facts", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        facts = json.loads(result.stdout.strip())
        assert facts["localEngineImageDigest"] == "sha256:engineexample"
        assert facts["localCompanionImageDigest"] == "sha256:adsexample"

    def test_local_engine_image_digest_is_null_when_not_pulled_or_not_digest_pinned(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        # Not pulled yet, even though digest-pinned.
        env = _base_env(
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=podman_log,
            image_local=False,
            extra_env={"SAFENT_IMAGE": "ghcr.io/devwspito/safent@sha256:engineexample"},
        )
        result = _run_safent("facts", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert json.loads(result.stdout.strip())["localEngineImageDigest"] is None

        # Bare tag, not digest-pinned (plain terminal use) — never claims a match.
        env2 = _base_env(
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home2",
            podman_log=podman_log,
            image_local=True,
        )
        result2 = _run_safent("facts", env=env2)
        assert result2.returncode == 0
        assert json.loads(result2.stdout.strip())["localEngineImageDigest"] is None
        assert json.loads(result2.stdout.strip())["localCompanionImageDigest"] is None


class TestEnsureMachineOnLinuxIsANoOp:
    def test_ensure_machine_closes_immediately_without_touching_podman_machine(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        result = _run_safent("ensure-machine", "--porcelain", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        assert [e["t"] for e in events] == ["stage", "done"]
        assert events[0]["id"] == "machine"
        assert not any(c.startswith("machine ") for c in _podman_calls(podman_log))

    def test_non_porcelain_prints_human_text_to_stderr_not_stdout(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        result = _run_safent("ensure-machine", env=env)
        assert result.returncode == 0
        assert result.stdout == ""
        assert "[ok]" in result.stderr


class TestStageRuntime:
    def test_without_a_bundle_manifest_is_a_harmless_noop(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        result = _run_safent("stage-runtime", "--porcelain", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        assert [e["t"] for e in events] == ["stage", "done"]

    def test_verified_binaries_are_staged_under_state_home_with_their_manifest_mode(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        home_dir = tmp_path / "home"
        engine_dir = _make_bundle(
            tmp_path, entries=[("podman", b"fake-podman-binary", "0755")]
        )
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=home_dir, podman_log=podman_log)
        result = subprocess.run(
            ["sh", str(engine_dir / "safent"), "stage-runtime", "--porcelain"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        assert [e["t"] for e in events] == ["stage", "progress", "done"]

        staged = home_dir / ".safent" / "runtime" / "6.1.1" / "podman"
        assert staged.read_bytes() == b"fake-podman-binary"
        assert (home_dir / ".safent" / "runtime" / "6.1.1" / ".verified").exists()

    def test_hash_mismatch_fails_closed_without_staging_anything(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        home_dir = tmp_path / "home"
        engine_dir = _make_bundle(tmp_path, entries=[("podman", b"fake-podman-binary", "0755")])
        # Corrupt the manifest's sha256 for the one entry AFTER the fact.
        manifest_path = engine_dir / "runtime-bundle.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["entries"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, indent=2))

        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=home_dir, podman_log=podman_log)
        result = subprocess.run(
            ["sh", str(engine_dir / "safent"), "stage-runtime", "--porcelain"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 14, f"stdout={result.stdout}\nstderr={result.stderr}"
        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        failed = events[-1]
        assert failed["t"] == "failed"
        assert failed["code"] == "runtime_hash_mismatch"
        assert failed["retryable"] is False
        assert not (home_dir / ".safent" / "runtime" / "6.1.1" / "podman").exists()


class TestEnsureImages:
    def test_success_emits_pull_engine_stage(self, tmp_path: Path, fake_bin_dir: Path) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        result = _run_safent("ensure-images", "--porcelain", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        assert events[0] == {"t": "stage", "id": "pull_engine", "label": events[0]["label"]}
        assert events[-1]["t"] == "done"
        assert any(c.startswith("pull ") for c in _podman_calls(podman_log))

    def test_registry_unreachable_fails_closed_with_the_documented_exit_code(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(
            fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log, pull_fails=True
        )
        result = _run_safent("ensure-images", "--porcelain", env=env)
        assert result.returncode == 19, f"stdout={result.stdout}\nstderr={result.stderr}"
        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        assert events[-1]["code"] == "registry_unreachable"
        assert events[-1]["retryable"] is True


def _run_up_with_secret_pipe(
    *args: str, env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run `up` with a real pipe open for the ticket descriptor, passed to
    the child at whatever fd number the OS actually gave it — forcing a
    SPECIFIC number (e.g. 3) via preexec_fn is unreliable with CPython's
    subprocess (close_fds runs AFTER preexec_fn, closing anything not in the
    ORIGINAL pass_fds set). --secret-fd is designed to be told the number
    instead, which is exactly what a real embedding wrapper would do too.

    Runs inside capsys.disabled(): pytest's default fd-level capture holds
    its own low-numbered descriptors open, which corrupts fd inheritance for
    a child that expects a specific extra fd — real terminal fds are needed
    here, same as any other test driving raw fd plumbing under pytest.
    Returns (result, ticket_text)."""
    r_fd, w_fd = os.pipe()
    os.set_inheritable(w_fd, True)
    try:
        with capsys.disabled():
            result = subprocess.run(
                ["sh", str(_SAFENT_CLI), "--no-companion", "up", *args, "--secret-fd", str(w_fd)],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                pass_fds=(w_fd,),
            )
    finally:
        os.close(w_fd)
    ticket = os.read(r_fd, 65536).decode()
    os.close(r_fd)
    return result, ticket


class TestUpDeliversTheTicketOnlyOnTheSecretFd:
    def test_porcelain_up_never_leaks_the_ticket_on_stdout_or_stderr(
        self, tmp_path: Path, fake_bin_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)

        result, ticket = _run_up_with_secret_pipe("--porcelain", env=env, capsys=capsys)

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert ticket.strip() == f"http://127.0.0.1:17517/?k={_SECRET_TOKEN}"
        assert _SECRET_TOKEN not in result.stdout
        assert _SECRET_TOKEN not in result.stderr

        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        assert [e["t"] for e in events] == ["stage", "done", "stage", "done", "ready"]
        assert events[-1] == {"t": "ready", "endpoint_ref": "stdout-secret"}

    def test_non_porcelain_up_prints_the_url_to_stdout_like_the_existing_url_verb(
        self, tmp_path: Path, fake_bin_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)

        result, ticket = _run_up_with_secret_pipe(env=env, capsys=capsys)

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert result.stdout.strip() == f"http://127.0.0.1:17517/?k={_SECRET_TOKEN}"
        assert ticket == ""  # non-porcelain `up` never touches --secret-fd

    def test_daemon_never_becoming_active_fails_closed(
        self, tmp_path: Path, fake_bin_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        podman_log = tmp_path / "podman.log"
        env = _base_env(
            fake_bin_dir=fake_bin_dir,
            home_dir=tmp_path / "home",
            podman_log=podman_log,
            health_active=False,
        )
        result, ticket = _run_up_with_secret_pipe("--porcelain", env=env, capsys=capsys)

        assert result.returncode == 24, f"stdout={result.stdout}\nstderr={result.stderr}"
        assert ticket == ""
        events = _parse_ndjson(result.stdout)
        _assert_stage_closure_invariant(events)
        assert events[-1]["t"] == "failed"
        assert events[-1]["code"] == "daemon_unhealthy"
        assert _SECRET_TOKEN not in result.stdout
        assert _SECRET_TOKEN not in result.stderr


class TestSafentPodmanOverridesPath:
    def test_facts_uses_safent_podman_even_when_path_podman_would_fail(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        # PATH's `podman` fails hard on ANY invocation — proves it was never called.
        broken = fake_bin_dir / "podman"
        broken.write_text("#!/usr/bin/env bash\nexit 97\n")
        broken.chmod(0o755)

        pinned_dir = tmp_path / "pinned"
        pinned_dir.mkdir()
        pinned = pinned_dir / "podman"
        pinned.write_text(_FAKE_PODMAN)
        pinned.chmod(0o755)

        podman_log = tmp_path / "podman.log"
        env = _base_env(fake_bin_dir=fake_bin_dir, home_dir=tmp_path / "home", podman_log=podman_log)
        env["SAFENT_PODMAN"] = str(pinned)

        result = _run_safent("facts", env=env)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        facts = json.loads(result.stdout.strip())
        assert facts["engineContainer"]["running"] is True
