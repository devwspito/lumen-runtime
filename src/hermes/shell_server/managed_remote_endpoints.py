"""managed_remote_endpoints — owner-authorized URLs for MANAGED_REMOTE MCP servers.

`_grant_mcp_egress_for_managed_remote()` (agents_os/infrastructure/
dbus_runtime_service.py) derives the ONE host it greenlights on the MCP
netns's default-deny allow-list from a LOCAL, owner-controlled source only —
never from a bundle's own argv (a compromised bundle must not be able to
request a grant for an arbitrary host). Today that source is the Ed25519-
paired `instance_association.cloud_endpoint`, which only ever describes
"safent-control". This module is the SECOND such source, for managed-remote
servers that are NOT the paired control-plane (e.g. "safent-ads"): the owner
explicitly sets `slug -> https URL` here, through the SAME operator-authZ
D-Bus verb gate as `add_mcp_server` (set_managed_remote_endpoint, see
dbus_runtime_service.py).

Security posture (SSRF, mirrors hermes.instance.infrastructure.
http_control_plane_client._validate_cloud_endpoint, but STRICTER):
  - https:// only.
  - IP literals are rejected OUTRIGHT (not just private/loopback/link-local
    ranges) — the ads control-plane must be reached by a DNS name the owner
    can rotate/revoke, consistent with cert validation and defense-in-depth;
    this also makes the "private/loopback/link-local" carve-out moot (any IP
    literal is already rejected, public or private).
  - well-known unsafe hostnames ("localhost", the GCP metadata name) blocked
    by name.
  - port must be 443 (the https default) unless the port is omitted — no
    other port is accepted, closing off internal-service-on-odd-port pivots.

Infrastructure layer: filesystem-backed (JSON), no framework dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

_ENDPOINTS_PATH = Path("/var/lib/hermes/managed-remote-endpoints.json")

# Hostnames blocked by name regardless of DNS resolution — mirrors
# http_control_plane_client._validate_cloud_endpoint's own list.
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal", "metadata"})

_ALLOWED_SCHEME = "https"
_ALLOWED_PORT = 443


class ManagedRemoteEndpointError(ValueError):
    """Raised when a managed_remote_endpoints slug or URL fails validation."""


def validate_managed_remote_endpoint_url(url: str) -> str:
    """Validate an owner-supplied managed-remote endpoint URL, return its hostname.

    Raises ManagedRemoteEndpointError (never returns) on any violation:
    non-https scheme, missing/blocked/IP-literal hostname, or a port other
    than 443. Never performs a DNS lookup or network call — purely syntactic,
    fail-fast, no time-of-check/time-of-use gap.
    """
    parsed = urlparse(url)

    if parsed.scheme != _ALLOWED_SCHEME:
        raise ManagedRemoteEndpointError(
            f"managed_remote endpoint must use https:// (got '{parsed.scheme}://')"
        )

    hostname = parsed.hostname or ""
    if not hostname:
        raise ManagedRemoteEndpointError("managed_remote endpoint must have a hostname")

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise ManagedRemoteEndpointError(
            f"managed_remote endpoint points at a blocked hostname: '{hostname}'"
        )

    if _looks_like_ip_literal(hostname):
        raise ManagedRemoteEndpointError(
            f"managed_remote endpoint must be a DNS name, not an IP literal: '{hostname}'"
        )

    if parsed.port is not None and parsed.port != _ALLOWED_PORT:
        raise ManagedRemoteEndpointError(
            f"managed_remote endpoint must use port {_ALLOWED_PORT} "
            f"(got {parsed.port})"
        )

    return hostname


def _looks_like_ip_literal(hostname: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    return True


def load_managed_remote_endpoints() -> dict[str, str]:
    """Return the persisted {slug: url} map, or {} if absent/corrupt."""
    try:
        data = json.loads(_ENDPOINTS_PATH.read_text())
        endpoints = data.get("endpoints", {})
        if not isinstance(endpoints, dict):
            return {}
        return {str(k): str(v) for k, v in endpoints.items()}
    except (OSError, json.JSONDecodeError):
        return {}


def get_managed_remote_endpoint(slug: str) -> str | None:
    """Return the owner-set URL for *slug*, or None if unset."""
    return load_managed_remote_endpoints().get(slug)


def save_managed_remote_endpoint(slug: str, url: str) -> None:
    """Validate *url* and persist it under *slug*, merged with existing entries.

    Raises ManagedRemoteEndpointError if the URL fails validation — the
    caller (the D-Bus verb) must not persist an invalid entry.
    """
    validate_managed_remote_endpoint_url(url)
    endpoints = load_managed_remote_endpoints()
    endpoints[slug] = url
    _ENDPOINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ENDPOINTS_PATH.write_text(json.dumps({"endpoints": endpoints}))
