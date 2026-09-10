"""GET /api/v1/system/update + runtime-manifest.json verification (T005,
contracts/update.md §2-3).

Two layers:
  - `hermes.shell_server.runtime_manifest` — pure fetch+verify, no HTTP.
    Fresh keypair per test, no hardcoded key material (same convention as
    tests/unit/config_sync/test_signature.py).
  - the HTTP route itself, via FastAPI's TestClient, with the network fetch
    monkeypatched at `_fetch_raw` — hermetic, no real request ever leaves
    the process (Constitution Principle V).

The one invariant tasks.md calls out explicitly: a manifest whose signature
does not verify must produce `update_available: false`, even when the
plain-text VERSION file says a newer version exists — fail-closed per
Constitution Principle IV ("manifiesto sin firma valida -> sin boton").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hermes
from hermes.shell_server import runtime_manifest as rm
from hermes.shell_server.system_update import create_system_update_router

pytestmark = pytest.mark.unit

_TOKEN = "test-bearer-token"  # noqa: S105 - test fixture, not a real credential


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key().public_bytes_raw().hex()


def _signed_manifest(
    private_key: Ed25519PrivateKey,
    *,
    version: str = "9.9.9",
    engine: dict[str, str] | None = None,
    companion: dict[str, dict[str, str]] | None = None,
    min_app_version: str | None = None,
) -> dict[str, object]:
    default_engine = {"linux/amd64": "sha256:" + "1" * 64}
    default_companion = {"safent-ads": {"linux/amd64": "sha256:" + "2" * 64}}
    payload: dict[str, object] = {
        "schema_version": 1,
        "version": version,
        "engine": engine if engine is not None else default_engine,
        "companion": companion if companion is not None else default_companion,
        "runtime_bundle": {"podman": "6.1.1", "machine_os": "6.1"},
        "min_app_version": min_app_version or version,
    }
    signature_hex = private_key.sign(rm.canonical_bytes(payload)).hex()
    return {**payload, rm.SIGNATURE_FIELD: signature_hex}


# ---------------------------------------------------------------------------
# Pure fetch+verify (no HTTP)
# ---------------------------------------------------------------------------


class TestFetchVerifiedManifest:
    def test_valid_signature_parses_the_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        private_key, pubkey_hex = _generate_keypair()
        raw = _signed_manifest(private_key, version="1.2.3")
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)

        manifest = rm.fetch_verified_manifest()

        assert manifest is not None
        assert manifest.version == "1.2.3"
        assert manifest.engine["linux/amd64"] == "sha256:" + "1" * 64

    def test_no_pubkey_configured_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        private_key, _pubkey_hex = _generate_keypair()
        raw = _signed_manifest(private_key)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", "")
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)

        assert rm.fetch_verified_manifest() is None

    def test_tampered_payload_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        private_key, pubkey_hex = _generate_keypair()
        raw = _signed_manifest(private_key, version="1.2.3")
        raw["version"] = "9.9.9"  # mutated AFTER signing
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)

        assert rm.fetch_verified_manifest() is None

    def test_signed_by_the_wrong_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        attacker_key, _attacker_pubkey = _generate_keypair()
        _real_key, real_pubkey_hex = _generate_keypair()
        raw = _signed_manifest(attacker_key)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", real_pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)

        assert rm.fetch_verified_manifest() is None

    def test_missing_signature_field_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _key, pubkey_hex = _generate_keypair()
        raw = {"schema_version": 1, "version": "1.2.3"}
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)

        assert rm.fetch_verified_manifest() is None

    def test_fetch_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _key, pubkey_hex = _generate_keypair()
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: None)

        assert rm.fetch_verified_manifest() is None

    def test_does_not_raise_on_non_dict_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _key, pubkey_hex = _generate_keypair()
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: ["not", "a", "dict"])

        assert rm.fetch_verified_manifest() is None


class TestPiecesForArch:
    def test_lists_engine_and_companion_digests_for_the_requested_arch_only(self) -> None:
        manifest = rm.RuntimeManifest(
            version="1.0.0",
            engine={"linux/amd64": "sha256:aaa", "linux/arm64": "sha256:bbb"},
            companion={"safent-ads": {"linux/amd64": "sha256:ccc", "linux/arm64": "sha256:ddd"}},
        )
        pieces = rm.pieces_for_arch(manifest, "linux/amd64")
        assert {"kind": "engine", "digest": "sha256:aaa"} in pieces
        assert {"kind": "companion", "slug": "safent-ads", "digest": "sha256:ccc"} in pieces
        assert not any(p.get("digest") == "sha256:bbb" for p in pieces)

    def test_missing_arch_entry_is_omitted_not_fabricated(self) -> None:
        manifest = rm.RuntimeManifest(
            version="1.0.0", engine={"linux/amd64": "sha256:aaa"}, companion={}
        )
        pieces = rm.pieces_for_arch(manifest, "linux/arm64")
        assert pieces == []


# ---------------------------------------------------------------------------
# HTTP route
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    app = FastAPI()
    app.state.shell_webui_token = _TOKEN
    app.include_router(create_system_update_router())
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


class TestGetSystemUpdateAuth:
    def test_missing_bearer_is_401(self) -> None:
        r = _client().get("/api/v1/system/update")
        assert r.status_code == 401

    def test_wrong_bearer_is_401(self) -> None:
        r = _client().get("/api/v1/system/update", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401


class TestGetSystemUpdateShape:
    def test_existing_fields_are_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Migration invariant (data-model.md): old consumers keep reading
        current_version/latest_version/update_available/updating unchanged."""
        import hermes.shell_server.system_update as su

        monkeypatch.setattr(su, "_fetch_latest", lambda: None)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", "")

        r = _client().get("/api/v1/system/update", headers=_auth_headers())
        assert r.status_code == 200
        body = r.json()
        for key in ("current_version", "latest_version", "update_available", "updating"):
            assert key in body

    def test_new_fields_are_present_and_null_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hermes.shell_server.system_update as su

        monkeypatch.setattr(su, "_fetch_latest", lambda: None)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", "")

        body = _client().get("/api/v1/system/update", headers=_auth_headers()).json()
        assert body["engine_digest"] is None
        assert body["companion_digest"] is None
        assert body["pieces"] == []


class TestUpdateAvailableIsFailClosedOnManifestSignature:
    def test_unsigned_manifest_means_no_button_even_if_version_text_is_newer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE T005 test tasks.md calls out by name: a newer plain-text
        VERSION alone must never flip update_available to true."""
        import hermes.shell_server.system_update as su

        monkeypatch.setattr(su, "_fetch_latest", lambda: "999.0.0")
        monkeypatch.setattr(hermes, "__version__", "0.1.0", raising=False)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", "")  # no manifest can ever verify

        body = _client().get("/api/v1/system/update", headers=_auth_headers()).json()
        assert body["update_available"] is False
        assert body["engine_digest"] is None

    def test_tampered_manifest_means_no_button(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import hermes.shell_server.system_update as su

        private_key, pubkey_hex = _generate_keypair()
        raw = _signed_manifest(private_key, version="999.0.0")
        raw["engine"] = {"linux/amd64": "sha256:" + "f" * 64}  # mutated after signing

        monkeypatch.setattr(su, "_fetch_latest", lambda: "999.0.0")
        monkeypatch.setattr(hermes, "__version__", "0.1.0", raising=False)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)

        body = _client().get("/api/v1/system/update", headers=_auth_headers()).json()
        assert body["update_available"] is False

    def test_valid_signed_manifest_with_a_newer_version_flips_the_button_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hermes.shell_server.system_update as su

        private_key, pubkey_hex = _generate_keypair()
        raw = _signed_manifest(
            private_key,
            version="999.0.0",
            engine={"linux/amd64": "sha256:" + "a" * 64},
            companion={"safent-ads": {"linux/amd64": "sha256:" + "b" * 64}},
        )

        monkeypatch.setattr(su, "_fetch_latest", lambda: "999.0.0")
        monkeypatch.setattr(hermes, "__version__", "0.1.0", raising=False)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)
        monkeypatch.setattr(rm, "current_arch_key", lambda: "linux/amd64")
        monkeypatch.setattr(su, "current_arch_key", lambda: "linux/amd64")

        body = _client().get("/api/v1/system/update", headers=_auth_headers()).json()
        assert body["update_available"] is True
        assert body["engine_digest"] == "sha256:" + "a" * 64
        assert body["companion_digest"] == "sha256:" + "b" * 64
        assert {"kind": "engine", "digest": "sha256:" + "a" * 64} in body["pieces"]

    def test_same_version_text_with_a_valid_manifest_stays_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Digest-vs-version nuance lives in the wrapper (RT-DESK); this
        endpoint's own gate is still the app semver comparison — a valid
        manifest alone, with no version bump, must not flip the button."""
        import hermes.shell_server.system_update as su

        private_key, pubkey_hex = _generate_keypair()
        raw = _signed_manifest(private_key, version="0.1.0")

        monkeypatch.setattr(su, "_fetch_latest", lambda: "0.1.0")
        monkeypatch.setattr(hermes, "__version__", "0.1.0", raising=False)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", pubkey_hex)
        monkeypatch.setattr(rm, "_fetch_raw", lambda: raw)

        body = _client().get("/api/v1/system/update", headers=_auth_headers()).json()
        assert body["update_available"] is False


class TestUpdatingReflectsInstallRequests:
    """T006 extracted the marker mechanism to install_requests.py; `updating`
    must still track it exactly via the shared `is_verb_live` read."""

    def test_false_with_no_live_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import hermes.shell_server.system_update as su

        monkeypatch.setattr(su, "_fetch_latest", lambda: None)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", "")

        body = _client().get("/api/v1/system/update", headers=_auth_headers()).json()
        assert body["updating"] is False

    def test_true_once_an_update_system_request_is_live(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import hermes.shell_server.install_requests as ir
        import hermes.shell_server.system_update as su

        monkeypatch.setattr(ir, "_INSTANCE_DIR", tmp_path / "instance")
        monkeypatch.setattr(su, "_fetch_latest", lambda: None)
        monkeypatch.setattr(rm, "_PUBKEY_HEX", "")

        ir.create_request("update_system")

        body = _client().get("/api/v1/system/update", headers=_auth_headers()).json()
        assert body["updating"] is True


class TestCanonicalBytesIsStableJson:
    def test_key_order_does_not_change_the_bytes(self) -> None:
        a = rm.canonical_bytes({"b": 1, "a": 2})
        b = rm.canonical_bytes({"a": 2, "b": 1})
        assert a == b
        assert json.loads(a) == {"a": 2, "b": 1}
