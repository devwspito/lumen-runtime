"""ops/container/sign_runtime_manifest.py — release tooling (T005).

No network, no registry calls (digests are passed in explicitly) — pure
assembly + signing, so this is fully hermetic. The important cross-module
guarantee proven here: a document this tool signs verifies with BOTH the
generic `hermes.config_sync.signature.verify_bundle` AND the daemon's own
`hermes.shell_server.runtime_manifest.canonical_bytes` — the two
independent canonical-encoding implementations must stay byte-identical or
every manifest this tool produces would silently fail to verify in prod.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hermes.config_sync.signature import verify_bundle
from hermes.shell_server import runtime_manifest as rm

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "ops" / "container" / "sign_runtime_manifest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sign_runtime_manifest", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


srm = _load_module()

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64
_DIGEST_D = "sha256:" + "d" * 64


class TestKeygen:
    def test_writes_a_0600_private_key_and_prints_a_64char_hex_pubkey(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "signing.key"
        rc = srm.main(["keygen", "--out-private", str(out)])
        assert rc == 0
        assert out.exists()
        assert stat.S_IMODE(out.stat().st_mode) == 0o600

        pubkey_hex = capsys.readouterr().out.strip()
        assert len(pubkey_hex) == 64  # 32-byte Ed25519 public key, hex-encoded
        int(pubkey_hex, 16)  # raises if not valid hex

    def test_two_runs_produce_different_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        srm.main(["keygen", "--out-private", str(tmp_path / "k1")])
        pub1 = capsys.readouterr().out.strip()
        srm.main(["keygen", "--out-private", str(tmp_path / "k2")])
        pub2 = capsys.readouterr().out.strip()
        assert pub1 != pub2


class TestSignPayload:
    def _payload(self) -> dict[str, object]:
        return srm.build_payload(
            version="1.2.3",
            engine_amd64=_DIGEST_A,
            engine_arm64=_DIGEST_B,
            companion_amd64=_DIGEST_C,
            companion_arm64=_DIGEST_D,
            podman_version="6.1.1",
            machine_os="6.1",
        )

    def test_signed_document_verifies_with_the_generic_verifier(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        pubkey_hex = private_key.public_key().public_bytes_raw().hex()
        document = srm.sign_payload(self._payload(), private_key)

        payload_only = {k: v for k, v in document.items() if k != srm.SIGNATURE_FIELD}
        assert verify_bundle(
            payload_canonical=srm.canonical_bytes(payload_only),
            signature_hex=document[srm.SIGNATURE_FIELD],
            pubkey_hex=pubkey_hex,
        )

    def test_signed_document_verifies_with_the_daemon_side_canonical_bytes(self) -> None:
        """The cross-module guarantee: srm.canonical_bytes and
        rm.canonical_bytes must be byte-identical for the same payload."""
        private_key = Ed25519PrivateKey.generate()
        pubkey_hex = private_key.public_key().public_bytes_raw().hex()
        document = srm.sign_payload(self._payload(), private_key)

        payload_only = {k: v for k, v in document.items() if k != rm.SIGNATURE_FIELD}
        assert rm.canonical_bytes(payload_only) == srm.canonical_bytes(payload_only)
        assert verify_bundle(
            payload_canonical=rm.canonical_bytes(payload_only),
            signature_hex=document[rm.SIGNATURE_FIELD],
            pubkey_hex=pubkey_hex,
        )

    def test_end_to_end_through_fetch_verified_manifest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The full loop: sign here, fetch+verify there."""
        private_key = Ed25519PrivateKey.generate()
        pubkey_hex = private_key.public_key().public_bytes_raw().hex()
        document = srm.sign_payload(self._payload(), private_key)

        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: document)

        manifest = rm.fetch_verified_manifest()
        assert manifest is not None
        assert manifest.version == "1.2.3"
        assert manifest.engine["linux/amd64"] == _DIGEST_A

    def test_rejects_a_tag_instead_of_a_digest(self) -> None:
        payload = srm.build_payload(
            version="1.2.3",
            engine_amd64="latest",  # not a digest
            engine_arm64=_DIGEST_B,
            companion_amd64=_DIGEST_C,
            companion_arm64=_DIGEST_D,
            podman_version="6.1.1",
            machine_os="6.1",
        )
        with pytest.raises(srm.DigestValidationError):
            srm.sign_payload(payload, Ed25519PrivateKey.generate())


class TestSignCli:
    def test_full_cli_round_trip_writes_a_valid_signed_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        key_path = tmp_path / "signing.key"
        srm.main(["keygen", "--out-private", str(key_path)])
        pubkey_hex = capsys.readouterr().out.strip()

        out_path = tmp_path / "runtime-manifest.json"
        rc = srm.main(
            [
                "sign",
                "--version", "2.0.0",
                "--engine-amd64", _DIGEST_A,
                "--engine-arm64", _DIGEST_B,
                "--companion-amd64", _DIGEST_C,
                "--companion-arm64", _DIGEST_D,
                "--podman-version", "6.1.1",
                "--machine-os", "6.1",
                "--private-key", str(key_path),
                "--out", str(out_path),
            ]
        )
        assert rc == 0
        document = json.loads(out_path.read_text())
        assert document["version"] == "2.0.0"
        assert document["min_app_version"] == "2.0.0"  # defaults to --version

        payload_only = {k: v for k, v in document.items() if k != srm.SIGNATURE_FIELD}
        assert verify_bundle(
            payload_canonical=srm.canonical_bytes(payload_only),
            signature_hex=document[srm.SIGNATURE_FIELD],
            pubkey_hex=pubkey_hex,
        )

    def test_cli_rejects_a_tag_and_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        key_path = tmp_path / "signing.key"
        srm.main(["keygen", "--out-private", str(key_path)])
        capsys.readouterr()

        out_path = tmp_path / "runtime-manifest.json"
        rc = srm.main(
            [
                "sign",
                "--version", "2.0.0",
                "--engine-amd64", "latest",
                "--engine-arm64", _DIGEST_B,
                "--companion-amd64", _DIGEST_C,
                "--companion-arm64", _DIGEST_D,
                "--podman-version", "6.1.1",
                "--machine-os", "6.1",
                "--private-key", str(key_path),
                "--out", str(out_path),
            ]
        )
        assert rc == 1
        assert not out_path.exists()
