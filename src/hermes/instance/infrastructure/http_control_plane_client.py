"""HttpControlPlaneClient — thin HTTP adapter for the enterprise control plane.

Implements ControlPlaneClient (Protocol) using httpx (already a project
dependency via shell_server).  All business logic lives in PairingService;
this adapter only handles serialization, network errors, and SSRF prevention.

Endpoints (relative to cloud_endpoint):
  POST /v1/associate  — begin_associate
  POST /v1/proof      — submit_proof

Error mapping:
  4xx from begin_associate → CodeInvalidError (generic message, no raw body echoed)
  4xx from submit_proof   → ChallengeFailedError (generic message)
  Network errors          → PairingError

Timeout: 30 s per request (one-shot operator action; not a hot path).

SSRF prevention:
  - Only https:// scheme accepted.
  - Loopback (127/8, ::1), link-local (169.254/16, fe80::/10), RFC1918
    (10/8, 172.16/12, 192.168/16), metadata endpoint (169.254.169.254),
    and the 0.0.0.0 wildcard are all rejected before any network call.
  - Validation happens in __init__ so misconfiguration is caught at
    construction time, not at the first request.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

import httpx

from hermes.instance.pairing_service import (
    ChallengeFailedError,
    CodeInvalidError,
    PairingError,
)

logger = logging.getLogger("hermes.instance.http_control_plane_client")

_TIMEOUT_S = 30.0

# RFC 1918 + loopback + link-local + metadata ranges that must never be targets.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
]


def _validate_cloud_endpoint(url: str) -> None:
    """Raise PairingError if the endpoint is not a safe https:// URL.

    Prevents SSRF to loopback, link-local, private, and metadata addresses.
    Called at construction time (fail-fast, no network involved).
    """
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise PairingError(
            f"cloud_endpoint debe usar esquema https:// (recibido: '{parsed.scheme}://')."
        )

    hostname = parsed.hostname or ""
    if not hostname:
        raise PairingError("cloud_endpoint debe tener un hostname válido.")

    # Reject well-known unsafe hostnames by name (catches "localhost" etc.).
    _BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata"}
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise PairingError(
            f"cloud_endpoint apunta a un host bloqueado por seguridad: '{hostname}'."
        )

    # Try to parse as IP and check against blocked networks.
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise PairingError(
                    f"cloud_endpoint apunta a una dirección bloqueada por seguridad: {addr}."
                )
    except ValueError:
        # hostname is a domain name, not an IP — allowed.
        pass


class HttpControlPlaneClient:
    """Concrete ControlPlaneClient that calls the real cloud endpoint.

    Construction raises PairingError immediately if cloud_endpoint is not
    a safe https:// URL (SSRF prevention — no lazy check).
    """

    def __init__(self, *, cloud_endpoint: str) -> None:
        _validate_cloud_endpoint(cloud_endpoint)
        # Strip trailing slash once; all paths are /v1/...
        self._base = cloud_endpoint.rstrip("/")

    def begin_associate(
        self,
        *,
        code: str,
        instance_id: str,
        hardware_fingerprint: str,
    ) -> dict:
        url = f"{self._base}/v1/associate"
        payload = {
            "code": code,
            "instance_id": instance_id,
            "hardware_fingerprint": hardware_fingerprint,
        }
        try:
            resp = httpx.post(url, json=payload, timeout=_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise PairingError("Error de red al contactar el control plane.") from exc

        if resp.status_code in (404, 410):
            raise CodeInvalidError(
                f"Código de asociación inválido o expirado (HTTP {resp.status_code})."
            )
        if resp.status_code >= 400:
            # Never echo resp.text to the client (P2: no raw cloud text in errors).
            logger.warning(
                "hermes.instance.begin_associate.error",
                extra={"status": resp.status_code},
            )
            raise CodeInvalidError(
                f"El control plane rechazó el código de asociación (HTTP {resp.status_code})."
            )

        return resp.json()

    def submit_proof(
        self,
        *,
        instance_id: str,
        proof_hex: str,
    ) -> dict:
        url = f"{self._base}/v1/proof"
        payload = {"instance_id": instance_id, "proof_hex": proof_hex}
        try:
            resp = httpx.post(url, json=payload, timeout=_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise PairingError("Error de red al enviar la prueba HMAC.") from exc

        if resp.status_code in (401, 403):
            raise ChallengeFailedError(
                f"El control plane rechazó la prueba HMAC (HTTP {resp.status_code})."
            )
        if resp.status_code >= 400:
            logger.warning(
                "hermes.instance.submit_proof.error",
                extra={"status": resp.status_code},
            )
            raise ChallengeFailedError(
                f"Fallo al enviar la prueba (HTTP {resp.status_code})."
            )

        return resp.json()
