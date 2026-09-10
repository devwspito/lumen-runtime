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

# gen_keys prints STANDARD base64 (the fake above) for BOTH the approval
# keypair (unchanged, T193/024) and the 026 SSO keypair (T001) —
# provision.sh calls gen_keys twice, once per pair. Kept byte-identical to
# the value test_gitleaks_allowlist.py pins (it must stay literally present
# in this file, or that allowlist entry becomes dead weight) — it happens
# to contain no `+`/`/`, so TestSsoKeypairUrlSafeConversion below uses ITS
# OWN fake with different values to actually exercise the alphabet swap.
_GEN_KEYS_PUBLIC = "ZmFrZS1wdWJsaWMta2V5LWI2NA=="
_GEN_KEYS_PUBLIC_URLSAFE = _GEN_KEYS_PUBLIC


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

    def test_leaf_key_is_readable_by_the_container_uid(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        # ads-api (uid 10001) monta tls/ de solo lectura: a 0600 del host no
        # podia leer la clave y entraba en bucle de arranque (T214). El
        # directorio de estado (0700) es lo que la protege de otros usuarios.
        state_dir, _ = provisioned_state
        assert _mode(state_dir / "tls" / "leaf.key") == 0o644
        assert _mode(state_dir) == 0o700

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


class TestSsoKeypairProvisioning:
    """026, contracts/sso.md §3 — the SSO Ed25519 pair provisioned alongside
    the bearer: private half 0400 on the host, public half handed to the
    companion via secrets/api.env, never argv/log."""

    def test_private_key_is_0400(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        assert _mode(state_dir / "sso" / "ads-sso.key") == 0o400

    def test_sso_dir_is_0700(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        assert _mode(state_dir / "sso") == 0o700

    def test_public_key_in_api_env_is_url_safe_base64(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, _ = provisioned_state
        api_env = (state_dir / "secrets" / "api.env").read_text()
        lines = {
            ln.split("=", 1)[0]: ln.split("=", 1)[1]
            for ln in api_env.splitlines()
            if "=" in ln and not ln.startswith("#")
        }
        assert lines["ADS_SSO_PUBLIC_KEY"] == _GEN_KEYS_PUBLIC_URLSAFE
        assert "+" not in lines["ADS_SSO_PUBLIC_KEY"]
        assert "/" not in lines["ADS_SSO_PUBLIC_KEY"]

    def test_private_key_never_reaches_stdout_or_stderr(
        self, provisioned_state: tuple[Path, subprocess.CompletedProcess[str]]
    ) -> None:
        state_dir, result = provisioned_state
        seed = (state_dir / "sso" / "ads-sso.key").read_text().strip()
        combined_output = result.stdout + result.stderr
        assert seed not in combined_output


# gen_keys public halves can legitimately contain base64's `+`/`/` chars;
# the shared fake above (_GEN_KEYS_PUBLIC) happens not to — it is pinned
# byte-identical to test_gitleaks_allowlist.py's allowlisted value. This
# fake is used ONLY by TestSsoKeypairUrlSafeConversion below, so it can use
# a value that actually exercises the standard->url-safe base64 swap
# without touching the gitleaks-pinned literal.
_FAKE_PODMAN_SPECIAL_CHARS_PUBLIC = _FAKE_PODMAN.replace(
    "ADS_APPROVAL_PUBLIC_KEY=ZmFrZS1wdWJsaWMta2V5LWI2NA==",
    "ADS_APPROVAL_PUBLIC_KEY=AAAA+BBBB/CCCC==",
)


class TestSsoKeypairUrlSafeConversion:
    """`tr '+/' '-_'` must actually swap the alphabet, not just pass a value
    through that never contained those characters (the everyday fake used
    elsewhere in this file for the gitleaks-allowlist reason above)."""

    def test_plus_and_slash_are_swapped_to_dash_and_underscore(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "fakebin"
        bin_dir.mkdir()
        podman = bin_dir / "podman"
        podman.write_text(_FAKE_PODMAN_SPECIAL_CHARS_PUBLIC)
        podman.chmod(0o755)
        curl = bin_dir / "curl"
        curl.write_text(_FAKE_CURL)
        curl.chmod(0o755)

        state_dir = tmp_path / "state"
        result = _run_provision(state_dir, bin_dir, tmp_path / "podman.log")

        assert result.returncode == 0, result.stderr
        api_env = (state_dir / "secrets" / "api.env").read_text()
        lines = {
            ln.split("=", 1)[0]: ln.split("=", 1)[1]
            for ln in api_env.splitlines()
            if "=" in ln and not ln.startswith("#")
        }
        assert lines["ADS_SSO_PUBLIC_KEY"] == "AAAA-BBBB_CCCC=="


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

    def test_second_run_does_not_regenerate_the_sso_keypair(
        self, tmp_path: Path, fake_bin_dir: Path
    ) -> None:
        state_dir = tmp_path / "state"
        podman_log = tmp_path / "podman.log"
        first = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert first.returncode == 0, first.stderr
        seed_before = (state_dir / "sso" / "ads-sso.key").read_bytes()
        pub_before = (state_dir / "secrets" / "api.env").read_text()

        second = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert second.returncode == 0, second.stderr
        assert (state_dir / "sso" / "ads-sso.key").read_bytes() == seed_before
        assert (state_dir / "secrets" / "api.env").read_text() == pub_before

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
            "GOOGLE_ADS_UNKNOWN=must-not-be-copied\n"
            "META_APP_ID=vendor-meta-app-id\n"
        )
        vendor_env.chmod(0o600)

        second = _run_provision(state_dir, fake_bin_dir, podman_log)
        assert second.returncode == 0, second.stderr
        broker_env_after_merge = (state_dir / "secrets" / "broker.env").read_text()
        assert "GOOGLE_ADS_CLIENT_ID=vendor-client-id" in broker_env_after_merge
        assert "GOOGLE_ADS_UNKNOWN" not in broker_env_after_merge
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


# =============================================================================
# T193 — `safent companion status|update|rotate|remove`
#
# Before this, `safent` had no dedicated companion lifecycle: `safent update`
# re-provisioned it as a SIDE EFFECT of recreating the Safent container, and
# `safent start` on an existing container did not even do that (runbook.md
# §9). These tests drive the REAL `safent` script (sh, not sourced) with only
# podman/curl faked — same "fake the two commands that would touch a real
# engine" discipline as TestFirstRunWritesExpectedFiles above.
# =============================================================================

_SAFENT_CLI = _REPO_ROOT / "safent"

# Args land on this fake exactly as the CLI invokes them:
#   network inspect safent-companions
#   network rm safent-companions
#   pull <image>
#   run --rm --network none <image> python -m safent_ads.tools.gen_keys
#   compose -p safent-ads -f <compose> ps -q -a
#   compose -p safent-ads -f <compose> up -d [--force-recreate ads-api]
#   compose -p safent-ads -f <compose> down
#   inspect -f {{.State.Running}} <id>
_FAKE_PODMAN_CLI = """#!/usr/bin/env bash
set -e
echo "$@" >> "$FAKE_PODMAN_LOG"
case "$1" in
  network)
    case "$2" in
      inspect) [ "${FAKE_NETWORK_PRESENT:-1}" = "1" ] && exit 0 || exit 1 ;;
      rm) exit 0 ;;
    esac
    ;;
  pull)
    [ "${FAKE_PULL_FAIL:-0}" = "1" ] && exit 1
    exit 0
    ;;
  run)
    [ "${FAKE_GEN_KEYS_FAIL:-0}" = "1" ] && exit 1
    for a in "$@"; do
      if [ "$a" = "safent_ads.tools.gen_keys" ]; then
        echo "ADS_APPROVAL_SIGNING_KEY=new-sso-seed-$RANDOM$RANDOM"
        echo "ADS_APPROVAL_PUBLIC_KEY=new+sso/pub-$RANDOM$RANDOM=="
        exit 0
      fi
    done
    exit 0
    ;;
  compose)
    verb="$6"
    case "$verb" in
      ps)
        # CLI-08: the REAL compose.yaml interpolates
        # ${ADS_POSTGRES_PASSWORD:?required} — a caller that forgot to
        # export it gets an interpolation error and an EMPTY `ps -q -a`,
        # not the container list. Model that exact failure instead of
        # ignoring the env entirely (a fake that always answers regardless
        # of env would never catch _companion_container_counts calling
        # compose WITHOUT `_companion_env` first).
        if [ -z "${ADS_POSTGRES_PASSWORD:-}" ]; then
          echo "required variable ADS_POSTGRES_PASSWORD is missing a value" >&2
          exit 0  # `|| true` in the CLI swallows this; ids stays empty either way
        fi
        for id in ${FAKE_COMPOSE_IDS:-c1 c2}; do echo "$id"; done
        exit 0
        ;;
      up) [ "${FAKE_COMPOSE_UP_FAIL:-0}" = "1" ] && exit 1; exit 0 ;;
      down) exit 0 ;;
    esac
    exit 0
    ;;
  inspect)
    id="$4"
    case " ${FAKE_RUNNING_IDS:-c1 c2} " in
      *" $id "*) echo true ;;
      *) echo false ;;
    esac
    exit 0
    ;;
esac
exit 0
"""

_FAKE_CURL_HEALTH = """#!/usr/bin/env bash
printf '%s' "${FAKE_HEALTH_CODE:-401}"
exit 0
"""


@pytest.fixture()
def fake_cli_bin_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    podman = bin_dir / "podman"
    podman.write_text(_FAKE_PODMAN_CLI)
    podman.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(_FAKE_CURL_HEALTH)
    curl.chmod(0o755)
    return bin_dir


def _companion_state(tmp_path: Path, *, provisioned: bool) -> Path:
    """A minimal $COMPANION_STATE — only what the CLI's own verbs read
    (bearer, secrets/api.env, tls/ca.crt, sso/ads-sso.key), never
    provision.sh's full output."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    if provisioned:
        (state_dir / "tls").mkdir()
        (state_dir / "tls" / "ca.crt").write_text("dummy-ca")
        (state_dir / "secrets").mkdir()
        (state_dir / "secrets" / "api.env").write_text(
            "ADS_MCP_TOKEN=old-bearer-value\n"
            "ADS_SESSION_SECRET=x\n"
            "ADS_SSO_PUBLIC_KEY=old-sso-pub\n"
        )
        (state_dir / "bearer").write_text("old-bearer-value\n")
        (state_dir / "bearer").chmod(0o400)
        (state_dir / "sso").mkdir()
        (state_dir / "sso" / "ads-sso.key").write_text("old-sso-seed\n")
        (state_dir / "sso" / "ads-sso.key").chmod(0o400)
    return state_dir


def _companion_bin_dir(home_dir: Path, *, provisioned: bool) -> Path:
    """$COMPANION_BIN_DIR derives from $HOME (safent has no override env for
    it) — mirrors run-safent.sh/provision.sh's own cached-file layout."""
    bin_dir = home_dir / ".safent" / "companions" / "ads" / "bin"
    bin_dir.mkdir(parents=True)
    if provisioned:
        shutil.copy(_COMPOSE_YAML, bin_dir / "compose.yaml")
    return bin_dir


def _run_companion(
    *args: str,
    fake_bin_dir: Path,
    state_dir: Path,
    home_dir: Path,
    podman_log: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin_dir}:{env.get('PATH', '')}"
    env["HOME"] = str(home_dir)
    env["SAFENT_COMPANION_STATE"] = str(state_dir)
    env["SAFENT_ADS_IMAGE"] = "safent-ads:test-fake"
    env["FAKE_PODMAN_LOG"] = str(podman_log)
    env.update(extra_env or {})
    return subprocess.run(
        ["sh", str(_SAFENT_CLI), "companion", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCompanionStatus:
    def test_reports_not_provisioned_when_never_provisioned(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=False)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=False)
        result = _run_companion(
            "status",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
        )
        assert result.returncode == 0, result.stderr
        assert "not provisioned" in result.stdout

    def test_reports_network_containers_and_health_when_provisioned(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        result = _run_companion(
            "status",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
            extra_env={
                "FAKE_NETWORK_PRESENT": "1",
                "FAKE_COMPOSE_IDS": "c1 c2 c3",
                "FAKE_RUNNING_IDS": "c1 c2",
                "FAKE_HEALTH_CODE": "401",
            },
        )
        assert result.returncode == 0, result.stderr
        assert "network:      up" in result.stdout
        assert "containers:   2/3 running" in result.stdout
        assert "/mcp/health:  reachable (HTTP 401)" in result.stdout

    def test_container_counts_are_not_zero_even_without_a_preexported_password(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        """CLI-08 root cause, reproduced exactly: `_run_companion` (this
        file's own harness, like a real shell) never exports
        ADS_POSTGRES_PASSWORD — `_companion_container_counts` MUST call
        `_companion_env` itself before invoking compose, or the fake's `ps`
        branch (modelling compose.yaml's real `${ADS_POSTGRES_PASSWORD:?...}`
        interpolation failure) returns no container IDs at all, exactly the
        `0/0 running` the matrix row reported against a companion whose 5
        containers were actually Up/healthy."""
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        result = _run_companion(
            "status",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
            extra_env={
                "FAKE_NETWORK_PRESENT": "1",
                "FAKE_COMPOSE_IDS": "c1 c2 c3 c4 c5",
                "FAKE_RUNNING_IDS": "c1 c2 c3 c4 c5",
            },
        )
        assert result.returncode == 0, result.stderr
        assert "containers:   0/0 running" not in result.stdout, result.stdout
        assert "containers:   5/5 running" in result.stdout

    def test_reports_network_absent(self, tmp_path: Path, fake_cli_bin_dir: Path) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        result = _run_companion(
            "status",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
            extra_env={"FAKE_NETWORK_PRESENT": "0"},
        )
        assert result.returncode == 0, result.stderr
        assert "network:      absent" in result.stdout


class TestCompanionUpdate:
    def test_pulls_the_image_and_recreates_the_containers(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        podman_log = tmp_path / "podman.log"
        result = _run_companion(
            "update",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=podman_log,
        )
        assert result.returncode == 0, result.stderr
        log = podman_log.read_text()
        assert "pull safent-ads:test-fake" in log
        assert "up -d" in log

    def test_fails_loud_when_not_provisioned(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=False)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=False)
        result = _run_companion(
            "update",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
        )
        assert result.returncode != 0
        assert "not provisioned" in result.stderr

    def test_fails_loud_when_pull_fails(self, tmp_path: Path, fake_cli_bin_dir: Path) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        result = _run_companion(
            "update",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
            extra_env={"FAKE_PULL_FAIL": "1"},
        )
        assert result.returncode != 0
        assert "Could not pull" in result.stderr


class TestCompanionRotate:
    def test_rotates_bearer_on_both_sides_and_restarts_ads_api(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        old_bearer = (state_dir / "bearer").read_text().strip()
        podman_log = tmp_path / "podman.log"

        result = _run_companion(
            "rotate",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=podman_log,
        )

        assert result.returncode == 0, result.stderr
        new_bearer = (state_dir / "bearer").read_text().strip()
        assert new_bearer != old_bearer
        assert len(new_bearer) == 64  # openssl rand -hex 32
        assert stat.S_IMODE((state_dir / "bearer").stat().st_mode) == 0o400

        api_env = (state_dir / "secrets" / "api.env").read_text()
        assert f"ADS_MCP_TOKEN={new_bearer}" in api_env
        assert "ADS_SESSION_SECRET=x" in api_env  # every other line survives
        assert api_env.count("ADS_MCP_TOKEN=") == 1

        # 026 — the SSO Ed25519 pair rotates alongside the bearer.
        new_sso_seed = (state_dir / "sso" / "ads-sso.key").read_text().strip()
        assert new_sso_seed != "old-sso-seed"
        assert stat.S_IMODE((state_dir / "sso" / "ads-sso.key").stat().st_mode) == 0o400
        assert "ADS_SSO_PUBLIC_KEY=old-sso-pub" not in api_env
        assert api_env.count("ADS_SSO_PUBLIC_KEY=") == 1
        new_sso_pub = next(
            ln.split("=", 1)[1]
            for ln in api_env.splitlines()
            if ln.startswith("ADS_SSO_PUBLIC_KEY=")
        )
        assert "+" not in new_sso_pub and "/" not in new_sso_pub  # url-safe b64

        log = podman_log.read_text()
        assert "run --rm --network none" in log
        assert "up -d --force-recreate ads-api" in log
        assert "restart safent" in result.stdout.lower()

    def test_fails_loud_when_sso_keygen_fails(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        old_bearer = (state_dir / "bearer").read_text().strip()

        result = _run_companion(
            "rotate",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
            extra_env={"FAKE_GEN_KEYS_FAIL": "1"},
        )

        assert result.returncode != 0
        # Bearer rotation already committed before the SSO step — the old
        # SSO key is left untouched rather than half-rotated.
        assert (state_dir / "bearer").read_text().strip() != old_bearer
        assert (state_dir / "sso" / "ads-sso.key").read_text().strip() == "old-sso-seed"

    def test_fails_loud_when_secrets_are_missing(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        (state_dir / "secrets" / "api.env").unlink()
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        result = _run_companion(
            "rotate",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
        )
        assert result.returncode != 0
        assert "not found" in result.stderr


class TestCompanionRemove:
    def test_composes_down_and_removes_the_network_keeping_state(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)
        podman_log = tmp_path / "podman.log"

        result = _run_companion(
            "remove",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=podman_log,
        )

        assert result.returncode == 0, result.stderr
        log = podman_log.read_text()
        assert "compose -p safent-ads" in log and "down" in log
        assert "network rm safent-companions" in log
        assert state_dir.exists()  # state survives without --purge
        assert (state_dir / "secrets" / "api.env").exists()

    def test_purge_also_deletes_state(self, tmp_path: Path, fake_cli_bin_dir: Path) -> None:
        state_dir = _companion_state(tmp_path, provisioned=True)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=True)

        result = _run_companion(
            "remove",
            "--purge",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
        )

        assert result.returncode == 0, result.stderr
        assert not state_dir.exists()


class TestCompanionUsageGuard:
    def test_unknown_verb_fails_loud_with_usage(
        self, tmp_path: Path, fake_cli_bin_dir: Path
    ) -> None:
        state_dir = _companion_state(tmp_path, provisioned=False)
        home_dir = tmp_path / "home"
        _companion_bin_dir(home_dir, provisioned=False)
        result = _run_companion(
            "bogus",
            fake_bin_dir=fake_cli_bin_dir,
            state_dir=state_dir,
            home_dir=home_dir,
            podman_log=tmp_path / "podman.log",
        )
        assert result.returncode != 0
        assert "Usage: safent companion" in result.stderr
