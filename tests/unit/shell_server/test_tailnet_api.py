"""Tests for the governed tailnet control API (spec 022).

Coverage:
  GET /tailnet — unconfigured (no status.json) → all-empty/false response
  GET /tailnet — configured → maps status.json fields, drops malformed peers
  GET /tailnet/peers — mirrors the peers list from status.json
  POST /connect — invalid auth-key shape → 422, nothing persisted/staged
  POST /connect — valid key → 202, vault.encrypt called, staged file has
                  action=connect + the plaintext key, key NEVER in the response
  POST /connect — vault write failure → 503
  POST /disconnect — valid password → 200, staged file has action=disconnect
  POST /disconnect — invalid chars → 400, nothing staged
  POST /disconnect — rate limited after N failures → 429
  POST /disconnect — response never contains the password
  Staged files are 0600
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.remote_access_tunnel.rate_limiter import PasswordRateLimiter
from hermes.shell_server.tailnet.api import create_tailnet_router

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def status_path(tmp_path: Path) -> Path:
    return tmp_path / "run" / "status.json"


@pytest.fixture
def control_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run" / "tailscale-control"
    d.mkdir(mode=0o700, parents=True)
    return d


@pytest.fixture
def vault_path(tmp_path: Path) -> Path:
    return tmp_path / "var-lib" / "tailscale-authkey.enc"


@pytest.fixture
def fake_vault() -> MagicMock:
    vault = MagicMock()
    vault.encrypt.return_value = b"\x00" * 12 + b"ciphertext-blob"
    return vault


@pytest.fixture
def fresh_limiter() -> PasswordRateLimiter:
    return PasswordRateLimiter()


@pytest.fixture
def client(
    status_path: Path,
    control_dir: Path,
    vault_path: Path,
    fake_vault: MagicMock,
    fresh_limiter: PasswordRateLimiter,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_tailnet_router(
            vault=fake_vault,
            status_path=status_path,
            control_dir=control_dir,
            vault_path=vault_path,
            rate_limiter=fresh_limiter,
        )
    )
    return TestClient(app)


def _staged(control_dir: Path) -> Path:
    return control_dir / "request.json"


VALID_AUTH_KEY = "tskey-auth-kABC123-defghijklmnopqrstuvwxyz012345"


# ---------------------------------------------------------------------------
# GET /tailnet
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_unconfigured_when_status_file_absent(self, client: TestClient) -> None:
        r = client.get("/api/v1/tailnet")
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "configured": False,
            "online": False,
            "node_name": None,
            "magicdns_suffix": None,
            "tailnet": None,
            "peers": [],
        }

    def test_configured_maps_status_fields(
        self, client: TestClient, status_path: Path
    ) -> None:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "node_name": "safent-agent",
                    "magicdns_suffix": "tail1234.ts.net",
                    "tailnet": "acme.ts.net",
                    "online": True,
                    "peers": [
                        {"name": "laptop", "online": True},
                        {"name": "server", "online": False},
                    ],
                }
            )
        )
        r = client.get("/api/v1/tailnet")
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["online"] is True
        assert body["node_name"] == "safent-agent"
        assert body["magicdns_suffix"] == "tail1234.ts.net"
        assert body["tailnet"] == "acme.ts.net"
        assert body["peers"] == [
            {"name": "laptop", "online": True},
            {"name": "server", "online": False},
        ]

    def test_malformed_peer_entries_are_dropped(
        self, client: TestClient, status_path: Path
    ) -> None:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "node_name": "safent-agent",
                    "magicdns_suffix": "tail1234.ts.net",
                    "tailnet": "acme.ts.net",
                    "online": True,
                    "peers": [
                        {"name": "laptop", "online": True},
                        {"online": True},  # missing name — dropped
                        "not-a-dict",  # dropped
                    ],
                }
            )
        )
        r = client.get("/api/v1/tailnet")
        assert r.json()["peers"] == [{"name": "laptop", "online": True}]

    def test_corrupt_json_treated_as_unconfigured(
        self, client: TestClient, status_path: Path
    ) -> None:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text("{not valid")
        r = client.get("/api/v1/tailnet")
        assert r.json()["configured"] is False

    def test_response_never_contains_secrets(
        self, client: TestClient, status_path: Path
    ) -> None:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "node_name": "safent-agent",
                    "magicdns_suffix": "tail1234.ts.net",
                    "tailnet": "acme.ts.net",
                    "online": True,
                    "peers": [],
                }
            )
        )
        r = client.get("/api/v1/tailnet")
        assert "auth_key" not in r.text
        assert "tskey" not in r.text


# ---------------------------------------------------------------------------
# GET /tailnet/peers
# ---------------------------------------------------------------------------


class TestGetPeers:
    def test_mirrors_status_peers(self, client: TestClient, status_path: Path) -> None:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps({"peers": [{"name": "laptop", "online": True}]})
        )
        r = client.get("/api/v1/tailnet/peers")
        assert r.status_code == 200
        assert r.json() == {"peers": [{"name": "laptop", "online": True}]}

    def test_empty_when_unconfigured(self, client: TestClient) -> None:
        r = client.get("/api/v1/tailnet/peers")
        assert r.json() == {"peers": []}


# ---------------------------------------------------------------------------
# POST /connect
# ---------------------------------------------------------------------------


class TestConnectEndpoint:
    def test_invalid_shape_returns_422(
        self, client: TestClient, control_dir: Path, fake_vault: MagicMock
    ) -> None:
        r = client.post("/api/v1/tailnet/connect", json={"auth_key": "not-a-real-key"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "invalid_auth_key"
        assert not _staged(control_dir).exists()
        fake_vault.encrypt.assert_not_called()

    def test_valid_key_returns_202(self, client: TestClient) -> None:
        r = client.post("/api/v1/tailnet/connect", json={"auth_key": VALID_AUTH_KEY})
        assert r.status_code == 202
        assert r.json() == {"staged": True}

    def test_valid_key_persists_via_vault(
        self, client: TestClient, fake_vault: MagicMock, vault_path: Path
    ) -> None:
        client.post("/api/v1/tailnet/connect", json={"auth_key": VALID_AUTH_KEY})
        fake_vault.encrypt.assert_called_once()
        _, kwargs = fake_vault.encrypt.call_args
        assert kwargs["plaintext"] == VALID_AUTH_KEY
        assert vault_path.exists()
        assert stat.S_IMODE(vault_path.stat().st_mode) == 0o600

    def test_valid_key_stages_control_request(
        self, client: TestClient, control_dir: Path
    ) -> None:
        client.post("/api/v1/tailnet/connect", json={"auth_key": VALID_AUTH_KEY})
        data = json.loads(_staged(control_dir).read_text())
        assert data["action"] == "connect"
        assert data["auth_key"] == VALID_AUTH_KEY
        assert "requested_at" in data

    def test_staged_file_permissions_0600(
        self, client: TestClient, control_dir: Path
    ) -> None:
        client.post("/api/v1/tailnet/connect", json={"auth_key": VALID_AUTH_KEY})
        mode = _staged(control_dir).stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_response_never_echoes_the_key(self, client: TestClient) -> None:
        r = client.post("/api/v1/tailnet/connect", json={"auth_key": VALID_AUTH_KEY})
        assert VALID_AUTH_KEY not in r.text

    def test_vault_write_failure_returns_503(
        self, client: TestClient, fake_vault: MagicMock
    ) -> None:
        fake_vault.encrypt.side_effect = OSError("disk full")
        r = client.post("/api/v1/tailnet/connect", json={"auth_key": VALID_AUTH_KEY})
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "vault_write_failed"

    def test_empty_key_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/tailnet/connect", json={"auth_key": ""})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /disconnect
# ---------------------------------------------------------------------------


class TestDisconnectEndpoint:
    def test_valid_password_returns_200_and_stages(
        self, client: TestClient, control_dir: Path
    ) -> None:
        r = client.post(
            "/api/v1/tailnet/disconnect", json={"password": "validpassword123"}
        )
        assert r.status_code == 200
        assert r.json() == {"staged": True}
        data = json.loads(_staged(control_dir).read_text())
        assert data["action"] == "disconnect"
        assert data["password"] == "validpassword123"

    def test_staged_file_permissions_0600(
        self, client: TestClient, control_dir: Path
    ) -> None:
        client.post("/api/v1/tailnet/disconnect", json={"password": "validpassword123"})
        mode = _staged(control_dir).stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_response_never_contains_password(self, client: TestClient) -> None:
        secret = "mysecretpassword99"
        r = client.post("/api/v1/tailnet/disconnect", json={"password": secret})
        assert secret not in r.text

    def test_invalid_chars_returns_400(
        self, client: TestClient, control_dir: Path
    ) -> None:
        r = client.post(
            "/api/v1/tailnet/disconnect", json={"password": "validpwd\x00evil"}
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_password"
        assert not _staged(control_dir).exists()

    def test_too_short_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/tailnet/disconnect", json={"password": "short"})
        assert r.status_code == 422

    def test_rate_limited_after_max_failures(
        self, control_dir: Path, status_path: Path, vault_path: Path, fake_vault: MagicMock
    ) -> None:
        limiter = PasswordRateLimiter(max_failures=5, window_seconds=60)
        app = FastAPI()
        app.include_router(
            create_tailnet_router(
                vault=fake_vault,
                status_path=status_path,
                control_dir=control_dir,
                vault_path=vault_path,
                rate_limiter=limiter,
            )
        )
        c = TestClient(app, raise_server_exceptions=False)
        for _ in range(5):
            c.post("/api/v1/tailnet/disconnect", json={"password": "validpassword1"})
        r = c.post("/api/v1/tailnet/disconnect", json={"password": "validpassword2"})
        assert r.status_code == 429
        assert r.json()["detail"]["code"] == "too_many_attempts"
