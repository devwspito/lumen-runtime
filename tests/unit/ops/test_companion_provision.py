"""ops/container/companions/ads/provision.sh — end-to-end host-side
provisioning (024). Runs the REAL script + the REAL compose.yaml/
caps.template.yaml (`HERE` resolves to the actual repo directory), with only
the two commands that would touch a real container engine or the network
faked: `podman` (network/image/gen_keys/compose) and `curl` (/mcp/health).
Everything else (openssl, sha256sum, mv, chmod...) is the real host tool,
exactly like a real run.

Covers: secrets are generated once and never re-generated, ADS_MCP_TOKEN
mirrors the bearer, broker.env carries the gen_keys public half, caps.yaml
is fail-closed (accounts: {}, autonomy_enabled: false), no secret value
ever reaches stdout/stderr, and a second run is a true no-op except for
merging an owner-provided vendor.env exactly once.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROVISION_SH = _REPO_ROOT / "ops/container/companions/ads/provision.sh"
_COMPOSE_YAML = _REPO_ROOT / "ops/container/companions/ads/compose.yaml"
_CAPS_TEMPLATE = _REPO_ROOT / "ops/container/companions/ads/caps.template.yaml"

_FAKE_PODMAN = """#!/usr/bin/env bash
set -e
echo "$@" >> "$FAKE_PODMAN_LOG"
case "$1 $2" in
  "network inspect")
    echo "10.201.0.0/24"
    exit 0
    ;;
  "network create")
    exit 0
    ;;
  "image inspect")
    exit 0
    ;;
esac
if [ "$1" = "run" ]; then
  for a in "$@"; do
    if [ "$a" = "safent_ads.tools.gen_keys" ]; then
      echo "ADS_APPROVAL_SIGNING_KEY=ZmFrZS1zaWduaW5nLWtleS1iNjQ="
      echo "ADS_APPROVAL_PUBLIC_KEY=ZmFrZS1wdWJsaWMta2V5LWI2NA=="
      exit 0
    fi
  done
  exit 0
fi
if [ "$1" = "compose" ] || [ "$1" = "pull" ]; then
  exit 0
fi
exit 0
"""

_FAKE_CURL = """#!/usr/bin/env bash
# /mcp/health is bearer-protected: a bare 401 IS liveness (see provision.sh).
printf '401'
exit 0
"""

_GEN_KEYS_PUBLIC = "ZmFrZS1wdWJsaWMta2V5LWI2NA=="


@pytest.fixture()
def fake_bin_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    podman = bin_dir / "podman"
    podman.write_text(_FAKE_PODMAN)
    podman.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(_FAKE_CURL)
    curl.chmod(0o755)
    return bin_dir


def _run_provision(
    state_dir: Path, fake_bin_dir: Path, podman_log: Path
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin_dir}:{env.get('PATH', '')}"
    env["SAFENT_COMPANION_STATE"] = str(state_dir)
    env["SAFENT_ADS_IMAGE"] = "safent-ads:test-fake"
    env["FAKE_PODMAN_LOG"] = str(podman_log)
    return subprocess.run(
        ["bash", str(_PROVISION_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _hash_tree(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


@pytest.fixture()
def provisioned_state(
    tmp_path: Path, fake_bin_dir: Path
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    state_dir = tmp_path / "state"
    podman_log = tmp_path / "podman.log"
    result = _run_provision(state_dir, fake_bin_dir, podman_log)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return state_dir, result


class TestFirstRunWritesExpectedFiles:
    def test_bearer_is_0400(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        assert _mode(state_dir / "bearer") == 0o400

    def test_api_env_is_0600_and_mcp_token_equals_the_bearer(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        api_env = state_dir / "secrets" / "api.env"
        assert _mode(api_env) == 0o600
        bearer = (state_dir / "bearer").read_text().strip()
        lines = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in api_env.read_text().splitlines() if "=" in ln and not ln.startswith("#")}
        assert lines["ADS_MCP_TOKEN"] == bearer
        assert lines["ADS_APPROVAL_SIGNING_KEY"] == "ZmFrZS1zaWduaW5nLWtleS1iNjQ="
        for required in ("ADS_SESSION_SECRET", "ADS_TOTP_ENC_KEY"):
            assert lines[required], f"{required} missing or empty"

    def test_broker_env_is_0600_and_has_the_public_key_from_gen_keys(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        broker_env = state_dir / "secrets" / "broker.env"
        assert _mode(broker_env) == 0o600
        text = broker_env.read_text()
        assert f"ADS_APPROVAL_PUBLIC_KEY={_GEN_KEYS_PUBLIC}" in text
        assert "ADS_BROKER_ALLOWED_UIDS=10001" in text
        assert "ADS_BROKER_HARD_CAPS_FILE=/etc/ads-broker/caps.yaml" in text

    def test_caps_yaml_is_fail_closed(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        caps_path = state_dir / "caps.yaml"
        assert _mode(caps_path) == 0o644
        doc = yaml.safe_load(caps_path.read_text())
        assert doc["accounts"] == {}
        assert doc["defaults"]["autonomy_enabled"] is False

    def test_companions_json_is_0444(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        assert _mode(state_dir / "companions.json") == 0o444

    def test_no_secret_value_reaches_stdout_or_stderr(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, result = provisioned_state
        api_env = (state_dir / "secrets" / "api.env").read_text()
        broker_env = (state_dir / "secrets" / "broker.env").read_text()
        secrets: list[str] = []
        for text in (api_env, broker_env):
            for line in text.splitlines():
                if "=" in line and not line.startswith("#"):
                    _, _, value = line.partition("=")
                    if value:
                        secrets.append(value)
        combined_output = result.stdout + result.stderr
        for secret in secrets:
            assert secret not in combined_output, f"secret value leaked into output: {secret!r}"


class TestSecondRunIsIdempotent:
    def test_second_run_changes_no_file(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        state_dir = tmp_path / "state"
        podman_log = tmp_path / "podman.log"
        first = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert first.returncode == 0, first.stderr
        before = _hash_tree(state_dir)

        second = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert second.returncode == 0, second.stderr
        after = _hash_tree(state_dir)

        assert before == after

    def test_vendor_env_is_merged_once_and_only_once(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        state_dir = tmp_path / "state"
        podman_log = tmp_path / "podman.log"
        first = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert first.returncode == 0, first.stderr

        vendor_env = state_dir / "vendor.env"
        vendor_env.write_text(
            "# owner-provided vendor credentials\n"
            "GOOGLE_ADS_CLIENT_ID=vendor-client-id\n"
            "META_APP_ID=vendor-meta-app-id\n"
        )
        vendor_env.chmod(0o600)

        second = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert second.returncode == 0, second.stderr
        broker_env_after_merge = (state_dir / "secrets" / "broker.env").read_text()
        assert "GOOGLE_ADS_CLIENT_ID=vendor-client-id" in broker_env_after_merge
        assert "META_APP_ID=vendor-meta-app-id" in broker_env_after_merge
        hash_after_merge = hashlib.sha256(
            (state_dir / "secrets" / "broker.env").read_bytes()
        ).hexdigest()

        third = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert third.returncode == 0, third.stderr
        hash_after_third_run = hashlib.sha256(
            (state_dir / "secrets" / "broker.env").read_bytes()
        ).hexdigest()

        assert hash_after_merge == hash_after_third_run
        assert broker_env_after_merge.count("GOOGLE_ADS_CLIENT_ID=") == 1
        assert broker_env_after_merge.count("META_APP_ID=") == 1


class TestComposeConfigRenders:
    """`podman compose config` / `docker compose config` with a dummy state
    — proves compose.yaml's variable interpolation and bind mounts resolve
    (not that the services actually boot, out of scope for a unit test)."""

    def test_compose_config_renders_with_dummy_state(self, tmp_path: Path) -> None:
        engine = shutil.which("podman") or shutil.which("docker")
        if engine is None:
            pytest.skip("neither podman nor docker is on PATH")
        probe = subprocess.run(
            [engine, "compose", "version"], capture_output=True, text=True, timeout=20
        )
        if probe.returncode != 0:
            pytest.skip(f"{engine} has no usable compose plugin")

        state_dir = tmp_path / "state"
        (state_dir / "tls").mkdir(parents=True)
        (state_dir / "tls" / "leaf.crt").write_text("dummy-cert")
        (state_dir / "tls" / "leaf.key").write_text("dummy-key")
        (state_dir / "secrets").mkdir()
        (state_dir / "secrets" / "api.env").write_text("ADS_MCP_TOKEN=dummy\n")
        (state_dir / "secrets" / "broker.env").write_text("ADS_APPROVAL_PUBLIC_KEY=dummy\n")
        shutil.copy(_CAPS_TEMPLATE, state_dir / "caps.yaml")

        env = dict(os.environ)
        env["SAFENT_STATE"] = str(state_dir)
        env["ADS_POSTGRES_PASSWORD"] = "x"
        env["SAFENT_ADS_IMAGE"] = "safent-ads:local"

        result = subprocess.run(
            [engine, "compose", "-f", str(_COMPOSE_YAML), "-p", "safent-ads", "config"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "10.201.0.10" in result.stdout
        assert "ADS_COMPANION_MODE" in result.stdout
