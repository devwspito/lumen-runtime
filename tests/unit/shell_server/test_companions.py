"""companions — /etc/hermes/companions.json loader + strict validation (024).

Covers: schema/scheme/host/subnet/port validation, DER-fingerprint cross-
check against the CA on disk, bearer_ref confinement to the companion
mount, ownership/permission enforcement (fail-soft to {} on ANY anomaly,
SC-3), and that the module exposes NO write path (INV-2 — see
TestCompanionsHasNoWritePath).
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from hermes.shell_server import companions as companions_mod
from hermes.shell_server.companions import (
    CompanionEndpoint,
    get_companion,
    load_companions,
    read_companion_bearer,
)

pytestmark = pytest.mark.unit


def _make_ca_pem() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "safent-ads test CA")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _fingerprint_of(pem: bytes) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
    return f"sha256:{hashlib.sha256(der).hexdigest()}"


@pytest.fixture()
def mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake /etc/hermes/companions/ mount dir + bypass the uid/perm gate
    (exercised separately in TestFileTrust) so schema tests focus on shape."""
    mount_dir = tmp_path / "companions"
    mount_dir.mkdir()
    monkeypatch.setattr(companions_mod, "_COMPANION_MOUNT_DIR", mount_dir)
    monkeypatch.setattr(companions_mod, "_is_trustworthy_file", lambda _path: True)
    return mount_dir


def _write_ca(mount_dir: Path) -> tuple[Path, str]:
    pem = _make_ca_pem()
    ca_path = mount_dir / "ads-ca.crt"
    ca_path.write_bytes(pem)
    return ca_path, _fingerprint_of(pem)


def _write_companions_json(
    tmp_path: Path, mount_dir: Path, *, overrides: dict | None = None
) -> Path:
    ca_path, fingerprint = _write_ca(mount_dir)
    bearer_path = mount_dir / "ads.bearer"
    bearer_path.write_text("s3cr3t-bearer-token\n")
    entry = {
        "slug": "safent-ads",
        "url": "https://ads.safent.internal:8443/mcp",
        "ip": "10.201.0.10",
        "port": 8443,
        "ca_path": str(ca_path),
        "ca_fingerprint": fingerprint,
        "bearer_ref": f"file:{bearer_path}",
    }
    entry.update(overrides or {})
    doc = {"version": 1, "companions": [entry]}
    path = tmp_path / "companions.json"
    path.write_text(json.dumps(doc))
    return path


class TestLoadCompanionsHappyPath:
    def test_valid_entry_loads(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(tmp_path, mount)
        result = load_companions(path=path)
        assert set(result) == {"safent-ads"}
        endpoint = result["safent-ads"]
        assert isinstance(endpoint, CompanionEndpoint)
        assert endpoint.url == "https://ads.safent.internal:8443/mcp"
        assert endpoint.ip == "10.201.0.10"
        assert endpoint.port == 8443

    def test_argv_never_contains_the_bearer(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(tmp_path, mount)
        endpoint = load_companions(path=path)["safent-ads"]
        assert endpoint.argv == [
            "npx", "-y", "mcp-remote@0.8.6", endpoint.url,
            "--header", "Authorization: Bearer ${ADS_BEARER}",
        ]
        assert "s3cr3t-bearer-token" not in " ".join(endpoint.argv)

    def test_get_companion_returns_the_slug(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(tmp_path, mount)
        assert get_companion("safent-ads", path=path) is not None
        assert get_companion("unknown-slug", path=path) is None

    def test_read_companion_bearer_reads_the_referenced_file(
        self, tmp_path: Path, mount: Path
    ) -> None:
        path = _write_companions_json(tmp_path, mount)
        endpoint = load_companions(path=path)["safent-ads"]
        assert read_companion_bearer(endpoint) == "s3cr3t-bearer-token"


@pytest.mark.usefixtures("mount")
class TestLoadCompanionsMissingOrMalformedFile:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_companions(path=tmp_path / "absent.json") == {}

    def test_malformed_json_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "companions.json"
        path.write_text("{not json")
        assert load_companions(path=path) == {}

    def test_wrong_version_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 2, "companions": []}))
        assert load_companions(path=path) == {}

    def test_companions_not_a_list_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": {}}))
        assert load_companions(path=path) == {}


class TestLoadCompanionsRejectsAnomalousEntries:
    """SC-3: a single bad field discards the WHOLE entry, fail-soft to {}."""

    def test_unknown_slug_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(tmp_path, mount, overrides={"slug": "safent-crm"})
        assert load_companions(path=path) == {}

    def test_http_scheme_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(
            tmp_path, mount, overrides={"url": "http://ads.safent.internal:8443/mcp"}
        )
        assert load_companions(path=path) == {}

    def test_wrong_port_field_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(tmp_path, mount, overrides={"port": 9443})
        assert load_companions(path=path) == {}

    def test_url_port_mismatch_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(
            tmp_path, mount, overrides={"url": "https://ads.safent.internal:9443/mcp"}
        )
        assert load_companions(path=path) == {}

    def test_host_outside_safent_internal_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(
            tmp_path, mount, overrides={"url": "https://ads.evil.example:8443/mcp"}
        )
        assert load_companions(path=path) == {}

    def test_ip_literal_host_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(
            tmp_path, mount, overrides={"url": "https://10.201.0.10:8443/mcp"}
        )
        assert load_companions(path=path) == {}

    def test_ip_outside_subnet_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(tmp_path, mount, overrides={"ip": "10.202.0.10"})
        assert load_companions(path=path) == {}

    def test_any_ip_inside_the_subnet_passes_schema_validation(
        self, tmp_path: Path, mount: Path
    ) -> None:
        # Not a special-cased rejection — merely proves ANY 10.201.0.0/24 value
        # is accepted at the SCHEMA layer (the nft generator is what pins the
        # ONE destination, see companion_nft.py's own tests).
        path = _write_companions_json(tmp_path, mount, overrides={"ip": "10.201.0.1"})
        assert "safent-ads" in load_companions(path=path)

    def test_not_an_ip_address_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(tmp_path, mount, overrides={"ip": "not-an-ip"})
        assert load_companions(path=path) == {}

    def test_fingerprint_mismatch_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        path = _write_companions_json(
            tmp_path, mount, overrides={"ca_fingerprint": "sha256:" + "0" * 64}
        )
        assert load_companions(path=path) == {}

    def test_ca_path_outside_mount_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        outside_ca = tmp_path / "outside-ca.crt"
        outside_ca.write_bytes(_make_ca_pem())
        path = _write_companions_json(tmp_path, mount, overrides={"ca_path": str(outside_ca)})
        assert load_companions(path=path) == {}

    def test_bearer_ref_outside_mount_is_dropped(self, tmp_path: Path, mount: Path) -> None:
        outside_bearer = tmp_path / "outside.bearer"
        outside_bearer.write_text("x")
        path = _write_companions_json(
            tmp_path, mount, overrides={"bearer_ref": f"file:{outside_bearer}"}
        )
        assert load_companions(path=path) == {}

    def test_bearer_ref_without_file_scheme_is_dropped(
        self, tmp_path: Path, mount: Path
    ) -> None:
        path = _write_companions_json(
            tmp_path, mount, overrides={"bearer_ref": "http://attacker.example/bearer"}
        )
        assert load_companions(path=path) == {}

    @pytest.mark.usefixtures("mount")
    def test_entry_not_an_object_is_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": ["not-an-object"]}))
        assert load_companions(path=path) == {}


class TestFileTrust:
    """Ownership/permission gate — exercised WITHOUT the `mount` fixture's bypass.

    Invariant (see `_is_trustworthy_file`): no group/other write bit AND
    (owned by uid 0 OR on a read-only mount and not owned by our own uid).
    """

    def test_non_root_owned_file_on_a_read_write_mount_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))

        real_stat = Path.stat

        class _FakeStat:
            st_uid = 1000
            st_mode = 0o100600

        def fake_stat(self: Path, *a: object, **kw: object) -> object:
            return _FakeStat() if self == path else real_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", fake_stat)
        assert load_companions(path=path) == {}

    def test_group_writable_file_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))

        real_stat = Path.stat

        class _FakeStat:
            st_uid = 0
            st_mode = 0o100640  # group-writable

        def fake_stat(self: Path, *a: object, **kw: object) -> object:
            return _FakeStat() if self == path else real_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", fake_stat)
        assert load_companions(path=path) == {}

    def test_world_writable_file_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))

        real_stat = Path.stat

        class _FakeStat:
            st_uid = 0
            st_mode = 0o100604  # other-writable

        def fake_stat(self: Path, *a: object, **kw: object) -> object:
            return _FakeStat() if self == path else real_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", fake_stat)
        assert load_companions(path=path) == {}

    def test_root_owned_read_only_file_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))

        real_stat = Path.stat

        class _FakeStat:
            st_uid = 0
            st_mode = 0o100644

        def fake_stat(self: Path, *a: object, **kw: object) -> object:
            return _FakeStat() if self == path else real_stat(self, *a, **kw)

        monkeypatch.setattr(Path, "stat", fake_stat)
        assert load_companions(path=path) == {}  # empty companions list, but FILE trusted


class TestFileTrustOnRootfulPodman:
    """Rootful podman does NOT remap uids: `provision.sh`'s file arrives as the
    installing owner's uid, not 0. It is still untamperable — the bind mount is
    `:ro` — so the loader accepts THAT shape too (024 rootful boot).
    """

    @staticmethod
    def _pin(
        monkeypatch: pytest.MonkeyPatch,
        path: Path,
        *,
        st_uid: int,
        st_mode: int,
        read_only_mount: bool,
        geteuid: int,
    ) -> None:
        real_stat = Path.stat

        class _FakeStat:
            pass

        _FakeStat.st_uid = st_uid  # type: ignore[attr-defined]
        _FakeStat.st_mode = st_mode  # type: ignore[attr-defined]

        def fake_stat(self: Path, *a: object, **kw: object) -> object:
            return _FakeStat() if self == path else real_stat(self, *a, **kw)

        class _FakeStatvfs:
            f_flag = os.ST_RDONLY if read_only_mount else 0

        monkeypatch.setattr(Path, "stat", fake_stat)
        monkeypatch.setattr(companions_mod.os, "statvfs", lambda _p: _FakeStatvfs())
        monkeypatch.setattr(companions_mod.os, "geteuid", lambda: geteuid)

    def test_owner_uid_file_on_a_read_only_mount_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))
        self._pin(
            monkeypatch, path,
            st_uid=1000, st_mode=0o100444, read_only_mount=True, geteuid=880,
        )
        assert companions_mod._is_trustworthy_file(path) is True

    def test_read_only_mount_does_not_excuse_a_group_writable_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))
        self._pin(
            monkeypatch, path,
            st_uid=1000, st_mode=0o100464, read_only_mount=True, geteuid=880,
        )
        assert companions_mod._is_trustworthy_file(path) is False

    def test_file_owned_by_the_daemon_user_is_rejected_even_on_a_read_only_mount(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The daemon could chmod +w its own file if the mount were ever
        remounted read-write — that is not a companion we trust."""
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))
        self._pin(
            monkeypatch, path,
            st_uid=880, st_mode=0o100444, read_only_mount=True, geteuid=880,
        )
        assert companions_mod._is_trustworthy_file(path) is False

    def test_root_owned_file_needs_no_read_only_mount(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rootless podman/docker remap the owner to 0 — branch (a) still holds."""
        path = tmp_path / "companions.json"
        path.write_text(json.dumps({"version": 1, "companions": []}))
        self._pin(
            monkeypatch, path,
            st_uid=0, st_mode=0o100444, read_only_mount=False, geteuid=880,
        )
        assert companions_mod._is_trustworthy_file(path) is True


class TestProvisionScriptMatchesTheLoaderGate:
    """`provision.sh` must WRITE the shape the loader accepts (024)."""

    @staticmethod
    def _provision_text() -> str:
        return (
            Path(__file__).resolve().parents[3]
            / "ops/container/companions/ads/provision.sh"
        ).read_text(encoding="utf-8")

    def test_companions_json_is_written_read_only_for_everyone(self) -> None:
        assert "chmod 0444" in self._provision_text()

    def test_companions_json_ownership_is_handed_to_root_when_possible(self) -> None:
        text = self._provision_text()
        assert "chown 0:0" in text

    def test_no_group_or_other_write_bit_is_ever_set_on_companions_json(self) -> None:
        text = self._provision_text()
        assert "chmod 0644 \"$STATE/companions.json\"" not in text


class TestCompanionsHasNoWritePath:
    """INV-2: no function in this module persists companions.json."""

    def test_module_exposes_no_save_or_write_function(self) -> None:
        public_names = [n for n in dir(companions_mod) if not n.startswith("_")]
        assert not any("save" in n.lower() or "write" in n.lower() for n in public_names)
