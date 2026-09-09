"""The daemon (uid 880) must be able to READ the companion bearer (024).

`read_companion_bearer` runs inside hermes-runtime.service as uid 880. The
bearer is a HOST file bind-mounted read-only, written 0400 by provision.sh, and
its uid/gid inside the container is an ENGINE artefact — 0:0 under rootless
podman/docker, the installing owner under rootful podman. Neither is `hermes`,
so the daemon could never read it and `ADS_BEARER` stayed empty: mcp-remote sent
no credential, /mcp answered 401, and the seeded server sat on
`companion_status: esperando_servicio` forever.

Widening the host mode to 0444 is not an option — uid 886 (`hermes-sandbox`, the
agent/MCP sandbox) would get the bearer. So a full-root `ExecStartPre=-+` stages
a 0440 root:hermes copy on tmpfs. These tests pin both halves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.shell_server import companions as companions_mod
from hermes.shell_server.companions import (
    COMPANION_RUNTIME_BEARER_DIR,
    CompanionEndpoint,
    read_companion_bearer,
    runtime_bearer_path,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _endpoint(bearer_ref: str) -> CompanionEndpoint:
    return CompanionEndpoint(
        slug="safent-ads",
        url="https://ads.safent.internal:8443/mcp",
        host="ads.safent.internal",
        ip="10.201.0.10",
        port=8443,
        ca_path="/etc/hermes/companions/ads-ca.crt",
        ca_fingerprint="sha256:" + "a" * 64,
        bearer_ref=bearer_ref,
    )


class TestReadCompanionBearerPrefersTheStagedCopy:
    def test_staged_copy_wins_over_the_unreadable_mount(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        staged = tmp_path / "safent-ads.bearer"
        staged.write_text("staged-token\n")
        monkeypatch.setattr(companions_mod, "runtime_bearer_path", lambda _s: str(staged))
        # bearer_ref points outside the mount => branch 2 would return None
        assert read_companion_bearer(_endpoint("file:/nowhere/ads.bearer")) == "staged-token"

    def test_falls_back_to_the_mount_when_nothing_is_staged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mount = tmp_path / "ads.bearer"
        mount.write_text("mount-token\n")
        monkeypatch.setattr(
            companions_mod, "runtime_bearer_path", lambda _s: str(tmp_path / "absent")
        )
        monkeypatch.setattr(companions_mod, "_COMPANION_MOUNT_DIR", tmp_path)
        assert read_companion_bearer(_endpoint(f"file:{mount}")) == "mount-token"

    def test_prefer_runtime_copy_false_reads_the_source_not_last_boots_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stage-in script MUST read the mount, or a rotated bearer would be
        invisible until the tmpfs is cleared."""
        staged = tmp_path / "safent-ads.bearer"
        staged.write_text("stale-token\n")
        mount = tmp_path / "ads.bearer"
        mount.write_text("rotated-token\n")
        monkeypatch.setattr(companions_mod, "runtime_bearer_path", lambda _s: str(staged))
        monkeypatch.setattr(companions_mod, "_COMPANION_MOUNT_DIR", tmp_path)
        got = read_companion_bearer(_endpoint(f"file:{mount}"), prefer_runtime_copy=False)
        assert got == "rotated-token"

    def test_staged_path_is_derived_from_the_slug_never_from_the_json(self) -> None:
        assert runtime_bearer_path("safent-ads") == f"{COMPANION_RUNTIME_BEARER_DIR}/safent-ads.bearer"
        assert COMPANION_RUNTIME_BEARER_DIR == "/run/hermes/companions"

    def test_out_of_mount_bearer_ref_is_still_refused_when_nothing_is_staged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "secret"
        outside.write_text("nope\n")
        monkeypatch.setattr(
            companions_mod, "runtime_bearer_path", lambda _s: str(tmp_path / "absent")
        )
        assert read_companion_bearer(_endpoint(f"file:{outside}")) is None


class TestStagingIsWiredIntoTheImage:
    def test_runtime_unit_stages_the_bearer_as_full_root_before_the_daemon(self) -> None:
        unit = (
            _REPO_ROOT / "ops/agents-os-edition/systemd/hermes-runtime.service"
        ).read_text(encoding="utf-8")
        assert "ExecStartPre=-+/usr/libexec/hermes/hermes-companion-bearer" in unit, (
            "the stage-in must run BEFORE the daemon and with the `+` full-privilege "
            "prefix — User=hermes cannot read the bind-mounted bearer."
        )
        assert unit.index("hermes-companion-bearer") < unit.index("ExecStart=/usr/bin/hermes-runtime")

    def test_script_is_baked_into_the_image(self) -> None:
        cf = (_REPO_ROOT / "ops/container/Containerfile").read_text(encoding="utf-8")
        assert "scripts/hermes-companion-bearer /usr/libexec/hermes/hermes-companion-bearer" in cf

    def test_staged_dir_is_root_owned_group_hermes_and_not_world_readable(self) -> None:
        conf = (
            _REPO_ROOT / "ops/agents-os-edition/tmpfiles/hermes.conf"
        ).read_text(encoding="utf-8")
        line = next(
            ln for ln in conf.splitlines() if ln.startswith("d /run/hermes/companions")
        )
        fields = line.split()
        assert fields[2] == "0750", "world-readable staged bearers would reach hermes-sandbox"
        assert fields[3] == "root"
        assert fields[4] == "hermes"

    def test_script_writes_0440_root_group_hermes(self) -> None:
        script = (
            _REPO_ROOT / "ops/agents-os-edition/scripts/hermes-companion-bearer"
        ).read_text(encoding="utf-8")
        assert "0o440" in script
        assert '_DAEMON_GROUP = "hermes"' in script
        assert "prefer_runtime_copy=False" in script
