"""managed_remote_endpoints — URL validation + owner-authorized persistence
(item 3, ads-vertical egress-grant second source).

Covers:
  - validate_managed_remote_endpoint_url: https-only, no IP literals, no
    blocked hostnames, port 443 only.
  - load/get/save_managed_remote_endpoints: filesystem persistence,
    fail-soft on missing/corrupt file, validation runs before persistence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.shell_server.managed_remote_endpoints import (
    ManagedRemoteEndpointError,
    get_managed_remote_endpoint,
    load_managed_remote_endpoints,
    save_managed_remote_endpoint,
    validate_managed_remote_endpoint_url,
)

pytestmark = pytest.mark.unit


class TestValidateManagedRemoteEndpointUrl:
    def test_valid_https_url_returns_hostname(self) -> None:
        assert (
            validate_managed_remote_endpoint_url("https://ads.tenant.ts.net/mcp")
            == "ads.tenant.ts.net"
        )

    def test_valid_https_url_no_path_returns_hostname(self) -> None:
        assert validate_managed_remote_endpoint_url("https://ads.example.com") == "ads.example.com"

    def test_explicit_port_443_accepted(self) -> None:
        assert (
            validate_managed_remote_endpoint_url("https://ads.example.com:443/mcp")
            == "ads.example.com"
        )

    def test_http_scheme_rejected(self) -> None:
        with pytest.raises(ManagedRemoteEndpointError):
            validate_managed_remote_endpoint_url("http://ads.example.com/mcp")

    def test_missing_scheme_rejected(self) -> None:
        with pytest.raises(ManagedRemoteEndpointError):
            validate_managed_remote_endpoint_url("ads.example.com/mcp")

    def test_missing_hostname_rejected(self) -> None:
        with pytest.raises(ManagedRemoteEndpointError):
            validate_managed_remote_endpoint_url("https:///mcp")

    def test_localhost_rejected(self) -> None:
        with pytest.raises(ManagedRemoteEndpointError):
            validate_managed_remote_endpoint_url("https://localhost/mcp")

    def test_metadata_hostname_rejected(self) -> None:
        with pytest.raises(ManagedRemoteEndpointError):
            validate_managed_remote_endpoint_url("https://metadata.google.internal/mcp")

    @pytest.mark.parametrize(
        "url",
        [
            "https://169.254.169.254/mcp",
            "https://127.0.0.1/mcp",
            "https://10.0.0.5/mcp",
            "https://192.168.1.1/mcp",
            "https://172.16.0.1/mcp",
            "https://8.8.8.8/mcp",  # public IP literal — still rejected (DNS-name only)
            "https://[::1]/mcp",
        ],
    )
    def test_ip_literal_hosts_rejected(self, url: str) -> None:
        with pytest.raises(ManagedRemoteEndpointError):
            validate_managed_remote_endpoint_url(url)

    @pytest.mark.parametrize("port", [80, 8080, 3128, 22, 6379])
    def test_non_443_port_rejected(self, port: int) -> None:
        with pytest.raises(ManagedRemoteEndpointError):
            validate_managed_remote_endpoint_url(f"https://ads.example.com:{port}/mcp")


class TestManagedRemoteEndpointsPersistence:
    def _patch_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        target = tmp_path / "managed-remote-endpoints.json"
        monkeypatch.setattr(
            "hermes.shell_server.managed_remote_endpoints._ENDPOINTS_PATH", target
        )
        return target

    def test_load_missing_file_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_path(monkeypatch, tmp_path)
        assert load_managed_remote_endpoints() == {}

    def test_load_corrupt_file_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = self._patch_path(monkeypatch, tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not json")
        assert load_managed_remote_endpoints() == {}

    def test_save_then_get_round_trips(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_path(monkeypatch, tmp_path)
        save_managed_remote_endpoint("safent-ads", "https://ads.tenant.ts.net/mcp")
        assert get_managed_remote_endpoint("safent-ads") == "https://ads.tenant.ts.net/mcp"

    def test_get_unset_slug_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._patch_path(monkeypatch, tmp_path)
        assert get_managed_remote_endpoint("safent-ads") is None

    def test_save_invalid_url_raises_and_does_not_persist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = self._patch_path(monkeypatch, tmp_path)
        with pytest.raises(ManagedRemoteEndpointError):
            save_managed_remote_endpoint("safent-ads", "http://ads.example.com/mcp")
        assert not target.exists()

    def test_save_merges_with_existing_entries(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        target = self._patch_path(monkeypatch, tmp_path)
        save_managed_remote_endpoint("safent-ads", "https://ads.tenant.ts.net/mcp")
        save_managed_remote_endpoint("other-remote", "https://other.tenant.ts.net/mcp")
        data = json.loads(target.read_text())
        assert data["endpoints"] == {
            "safent-ads": "https://ads.tenant.ts.net/mcp",
            "other-remote": "https://other.tenant.ts.net/mcp",
        }
