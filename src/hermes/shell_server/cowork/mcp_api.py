"""MCP servers REST API — D-Bus surface for MCP server management.

Endpoints:
  GET    /api/v1/mcp                                   list configured MCP servers
  POST   /api/v1/mcp                                    add a new MCP server
  DELETE /api/v1/mcp/{id}                               remove an MCP server
  GET    /api/v1/mcp/managed-remote-endpoints            list owner-set managed-remote URLs
  PUT    /api/v1/mcp/managed-remote-endpoints/{slug}     set the URL for a managed-remote slug
  POST   /api/v1/mcp/managed-remote/{slug}/connect       set the URL + register the server

Security:
  - Mutators carry a signed OperatorToken (DbusRuntimeProxy.call_mutator).
  - fail-soft for GET; fail-hard 503 for mutators (CTRL-P1-11).
  - managed-remote-endpoints GET reads the owner-authorized JSON store directly
    (hermes.shell_server.managed_remote_endpoints — filesystem-backed, no
    secrets, same pattern as egress_api.py's domain lists) — no D-Bus round
    trip for a read-only, non-secret listing.
  - managed-remote-endpoints PUT and managed-remote/connect go through the
    SAME operator-authZ D-Bus mutators as add_mcp_server
    (set_managed_remote_endpoint, add_mcp_server) — https-only/no-IP-literal
    validation and the install security-scan gate both apply unchanged.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hermes.shell_server.managed_remote_endpoints import load_managed_remote_endpoints
from hermes.tasks.control_plane.domain.ports import AgentUnavailable

logger = logging.getLogger("hermes.shell_server.cowork.mcp_api")

# Friendly labels for known managed-remote slugs (add_mcp_server persists
# whatever label we send). Falls back to a title-cased slug for one we don't
# know about yet — the daemon is the source of truth on which slugs are
# actually managed-remote (_MANAGED_REMOTE_MCP_SLUGS); this is display-only.
_MANAGED_REMOTE_LABELS: dict[str, str] = {
    "safent-ads": "Safent Ads",
    "safent-control": "Safent Control",
}


# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------


class AddMcpServerRequest(BaseModel):
    # Mirrors the daemon's add_mcp_server draft 1:1 (server_id/label/argv/env/force) so
    # the frontend → shell-server → daemon contract is a single shape. argv[0]
    # must be an allowed runner (npx/uvx/node/python3); the daemon validates.
    server_id: str = Field(min_length=1, max_length=120)
    label: str | None = Field(default=None)
    argv: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict, description="BYOK env vars")
    # Owner sovereign override: set to True only AFTER the owner's MFA was verified
    # by POST /api/v1/security/decisions (security_api.py → _require_owner_mfa).
    # The daemon re-checks with its own inline override logic (allow_target + rescan).
    # Without this field the frontend's force=true was silently dropped by Pydantic,
    # so the daemon always saw force=False and the FAIL gate was never lifted.
    force: bool = Field(default=False)


class SetManagedRemoteEndpointRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ConnectManagedRemoteRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    # Same sovereign-override contract as AddMcpServerRequest.force — set True
    # only after the owner approved a FAIL/WARN scan via POST /security/decisions.
    force: bool = Field(default=False)


# ------------------------------------------------------------------
# Router factory
# ------------------------------------------------------------------


def create_mcp_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

    @router.get("")
    async def list_mcp_servers(request: Request) -> list[dict]:
        """List configured MCP servers with status and tool_count.

        Fail-soft: returns [] when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_list("list_mcp_servers")
        except AgentUnavailable as exc:
            logger.warning(
                "hermes.mcp.list_unavailable",
                extra={"reason": str(exc)},
            )
            return []

    @router.post("", status_code=201)
    async def add_mcp_server(request: Request, body: AddMcpServerRequest) -> dict:
        """Register a new MCP server (local command or remote URL)."""
        proxy = request.app.state.dbus_proxy
        draft = {
            "server_id": body.server_id,
            "label": body.label or body.server_id,
            "argv": body.argv,
            "env": body.env,
            # Propagate the sovereign override flag so the daemon's scan gate sees it.
            # The MFA was already verified by the caller in POST /api/v1/security/decisions
            # before this add is retried with force=True.
            "force": body.force,
        }
        try:
            return await proxy.call_mutator("add_mcp_server", json.dumps(draft))
        except AgentUnavailable as exc:
            _raise_503(exc, "add_mcp_server")

    @router.delete("/{server_id}", status_code=204)
    async def remove_mcp_server(request: Request, server_id: str) -> None:
        """Remove a registered MCP server."""
        proxy = request.app.state.dbus_proxy
        try:
            await proxy.call_mutator("remove_mcp_server", server_id)
        except AgentUnavailable as exc:
            _raise_503(exc, "remove_mcp_server")

    @router.get("/registry")
    async def search_mcp_registry(request: Request, q: str = "", limit: int = 30) -> list[dict]:
        """Search the official MCP registry (registry.modelcontextprotocol.io).

        Proxies the daemon's search_mcp_registry, which returns entries already
        normalised to the add_mcp_server shape (server_id/label/argv/...). Parity
        with the native SO (McpApp.qml "registry" source). Fail-soft: [] on error.
        """
        if not q or len(q.strip()) < 2:
            return []
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_list("search_mcp_registry", q.strip(), int(limit))
        except AgentUnavailable as exc:
            logger.warning("hermes.mcp.registry_unavailable", extra={"reason": str(exc)})
            return []

    @router.get("/managed-remote-endpoints")
    async def list_managed_remote_endpoints() -> dict:
        """Owner-set {slug: url} for managed-remote MCP servers (e.g. safent-ads).

        Read-only, no secrets (hostnames only) — reads the local JSON store
        directly, no D-Bus round trip. Fail-soft: {} on a missing/corrupt file
        (load_managed_remote_endpoints already fails soft internally).
        """
        return {"endpoints": load_managed_remote_endpoints()}

    @router.put("/managed-remote-endpoints/{slug}")
    async def set_managed_remote_endpoint(
        request: Request, slug: str, body: SetManagedRemoteEndpointRequest
    ) -> dict:
        """Set the owner-authorized URL for a managed-remote MCP slug.

        The daemon validates https-only/no-IP-literal/port-443 and rejects a
        slug outside its own _MANAGED_REMOTE_MCP_SLUGS allowlist — this layer
        does not duplicate that check, it just surfaces {ok, error} as-is.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_mutator("set_managed_remote_endpoint", slug, body.url)
        except AgentUnavailable as exc:
            _raise_503(exc, "set_managed_remote_endpoint")

    @router.post("/managed-remote/{slug}/connect")
    async def connect_managed_remote(
        request: Request, slug: str, body: ConnectManagedRemoteRequest
    ) -> dict:
        """Set the endpoint URL and register the mcp-remote bridge, in one step.

        Idempotent: re-running with the same url re-points the endpoint and
        reconnects the SAME server_id (the daemon's add_mcp_server upserts by
        server_id). Stops after step 1 if the URL is rejected (invalid/not a
        known managed-remote slug) — never attempts to add a server pointed at
        an unvalidated URL. Step 2 goes through the SAME install security-scan
        gate as any other add_mcp_server call: a FAIL/WARN verdict comes back
        as {ok: false, blocked: true, scan_id, ...} unless `force` carries an
        already-recorded owner override (see AddMcpServerRequest.force).
        """
        proxy = request.app.state.dbus_proxy
        try:
            endpoint_result = await proxy.call_mutator(
                "set_managed_remote_endpoint", slug, body.url
            )
        except AgentUnavailable as exc:
            _raise_503(exc, "set_managed_remote_endpoint")
        if not endpoint_result.get("ok"):
            return endpoint_result

        draft = {
            "server_id": slug,
            "label": _MANAGED_REMOTE_LABELS.get(slug, slug.replace("-", " ").title()),
            "argv": ["npx", "-y", "mcp-remote", body.url],
            "env": {},
            "force": body.force,
        }
        try:
            return await proxy.call_mutator("add_mcp_server", json.dumps(draft))
        except AgentUnavailable as exc:
            _raise_503(exc, "add_mcp_server")

    return router


def _raise_503(exc: AgentUnavailable, operation: str) -> None:
    logger.warning(
        "hermes.mcp.mutator_unavailable",
        extra={"operation": operation, "reason": str(exc)},
    )
    raise HTTPException(
        status_code=503,
        detail={
            "code": "agent_unavailable",
            "message": "El agente no está disponible. Comprueba que hermes-runtime está activo.",
        },
    ) from exc
