"""CompanionHealthChecker — daemon-side `GET /mcp/health` reader (026, T004).

The daemon becomes the ONLY reader of the companion's CA + bearer for this
purpose (mirrors `_autowire_companion_env`'s own "read fresh, never persist"
discipline, INV-4): every health check re-reads
`hermes.shell_server.companions.get_companion`/`read_companion_bearer`
instead of caching either value.

`ads.safent.internal` is NOT resolvable by the container's normal DNS (it is
only ever reached via the fixed companion IP, 10.201.0.0/24) — exactly the
gap `provision.sh`'s own health probe plugs with `curl --resolve`. The
aiohttp equivalent is a resolver pinned to the validated `CompanionEndpoint`
IP while the request URL keeps the real hostname, so TLS SNI/hostname
verification against the companion's CA still checks the certificate's
actual SAN (`DNS:ads.safent.internal`).
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Final

import aiohttp

from hermes.shell_server.companion_net import FixedIpResolver

logger = logging.getLogger("hermes.agents_os.companion_health_check")

_HEALTH_PATH = "/mcp/health"
_CONNECT_TIMEOUT_S: Final = 5.0
_TOTAL_TIMEOUT_S: Final = 8.0
_HTTP_OK: Final = 200
_HTTP_UNAUTHORIZED: Final = 401


@dataclass(frozen=True)
class CompanionHealthReport:
    """Honest, derived-only state (FR-003/FR-009 — never a fabricated
    "ready"). `state` is the coarse signal `useAdsAvailability` (T009)
    switches on; `detail` carries the raw upstream fields for diagnostics,
    never a secret."""

    state: str  # "not_installed" | "unreachable" | "unauthorized" | "no_accounts" | "ready"
    reachable: bool
    http_status: int | None = None
    contract_version: str | None = None
    accounts_linked: dict[str, bool] | None = None


class CompanionHealthChecker:
    """One `check(slug)` entry point (SRP) — stateless, safe to reuse."""

    async def check(self, slug: str) -> CompanionHealthReport:
        from hermes.shell_server.companions import (  # noqa: PLC0415
            get_companion,
            read_companion_bearer,
        )

        endpoint = get_companion(slug)
        if endpoint is None:
            return CompanionHealthReport(state="not_installed", reachable=False)

        bearer = read_companion_bearer(endpoint)
        if not bearer:
            return CompanionHealthReport(state="not_installed", reachable=False)

        try:
            ssl_ctx = ssl.create_default_context(cafile=endpoint.ca_path)
        except (OSError, ssl.SSLError):
            logger.warning(
                "hermes.dbus.companion_health_ca_unreadable", extra={"slug": slug}
            )
            return CompanionHealthReport(state="unreachable", reachable=False)

        return await self._fetch(slug, endpoint=endpoint, bearer=bearer, ssl_ctx=ssl_ctx)

    async def _fetch(
        self, slug: str, *, endpoint, bearer: str, ssl_ctx: ssl.SSLContext
    ) -> CompanionHealthReport:
        resolver = FixedIpResolver(hostname=endpoint.host, ip=endpoint.ip)
        timeout = aiohttp.ClientTimeout(
            total=_TOTAL_TIMEOUT_S, connect=_CONNECT_TIMEOUT_S
        )
        connector = aiohttp.TCPConnector(resolver=resolver, ssl=ssl_ctx)
        url = f"https://{endpoint.host}:{endpoint.port}{_HEALTH_PATH}"
        try:
            async with (
                aiohttp.ClientSession(connector=connector, timeout=timeout) as session,
                session.get(
                    url, headers={"Authorization": f"Bearer {bearer}"}
                ) as response,
            ):
                return await self._interpret(slug, response)
        except (TimeoutError, aiohttp.ClientError, ssl.SSLError, OSError) as exc:
            logger.info(
                "hermes.dbus.companion_health_unreachable",
                extra={"slug": slug, "reason": type(exc).__name__},
            )
            return CompanionHealthReport(state="unreachable", reachable=False)

    async def _interpret(
        self, slug: str, response: aiohttp.ClientResponse
    ) -> CompanionHealthReport:
        if response.status == _HTTP_UNAUTHORIZED:
            logger.warning(
                "hermes.dbus.companion_health_unauthorized", extra={"slug": slug}
            )
            return CompanionHealthReport(
                state="unauthorized", reachable=True, http_status=_HTTP_UNAUTHORIZED
            )
        if response.status != _HTTP_OK:
            return CompanionHealthReport(
                state="unreachable", reachable=True, http_status=response.status
            )
        try:
            body = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            return CompanionHealthReport(
                state="unreachable", reachable=True, http_status=response.status
            )
        accounts = body.get("accounts_linked") if isinstance(body, dict) else None
        accounts_linked = accounts if isinstance(accounts, dict) else {}
        has_account = any(bool(v) for v in accounts_linked.values())
        state = "ready" if has_account else "no_accounts"
        return CompanionHealthReport(
            state=state,
            reachable=True,
            http_status=response.status,
            contract_version=(body.get("contract_version") if isinstance(body, dict) else None),
            accounts_linked=accounts_linked,
        )
