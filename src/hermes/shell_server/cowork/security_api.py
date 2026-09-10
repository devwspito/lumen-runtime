"""Security center REST API — D-Bus surface for scans, policy, and audit head.

Endpoints:
  GET  /api/v1/security/scans         list recent security scans
  GET  /api/v1/security/policy        get current security policy
  GET  /api/v1/security/audit/head    get audit chain head (integrity status)
  POST /api/v1/security/scans/install run a pre-install security scan
  POST /api/v1/security/decisions     record an install decision (approve/deny)

Security:
  - All reads are fail-soft (return degraded state, not 503).
  - Scan + decision are mutators; fail-hard 503 (CTRL-P1-11).

Owner-approval flow (Part 2 contract):
  1. Frontend calls POST /api/v1/security/scans/install with {kind, identifier}.
     Response shape:
       {
         scan_id: str,
         verdict: "PASS" | "WARN" | "FAIL",
         score: int (0–100),
         engine: "trivy" | "heuristic",
         engine_label: str,               # human-readable provenance
         requires_owner_approval: bool,   # true when verdict != "PASS"
         risks: [{category, severity, message, evidence_ref}]
       }
  2. If requires_owner_approval=true, show the risk list and an approval dialog.
  3. To approve, POST /api/v1/security/decisions with:
       {
         scan_id: str,        # from step 1
         decision: "approve", # or "allow" / "allow_once" / "installed"
         totp: str,           # owner TOTP code (required for override)
       }
     Response: { ok: true } on success, or 401/403 on MFA failure.
  4. After a successful decision the ScanService gate records decision=ALLOWED
     so the install verb (add_mcp_server / install_hub_skill / install_package)
     sees the override in cache and proceeds.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hermes.shell_server.security.mfa import MfaStore
from hermes.shell_server.security.owner_mfa_gate import issue_reauth_grant, require_owner_mfa
from hermes.tasks.control_plane.domain.ports import AgentUnavailable

logger = logging.getLogger("hermes.shell_server.cowork.security_api")

# Decisions that ELEVATE an install past a FAIL/WARN scan verdict (sovereign owner
# override, modelo "todo elevable"). These require the owner's TOTP (TOTP-only model),
# same bar as changing a security policy. A plain "deny"/"block" needs no MFA.
_OVERRIDE_DECISIONS = frozenset({"allow", "approve", "allowed", "allow_once", "install", "installed"})


# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------


class ScanInstallRequest(BaseModel):
    kind: str = Field(min_length=1, description="Target kind: skill, mcp, flatpak, rpm")
    identifier: str = Field(min_length=1, description="Package/skill identifier to scan")


class RecordInstallDecisionRequest(BaseModel):
    scan_id: str = Field(min_length=1)
    decision: str = Field(min_length=1, description="approve/allow or deny")
    identifier: str = ""
    kind: str = ""
    score: int = -1
    verdict: str = ""
    risks_json: str = "[]"
    totp: str | None = None          # required to ALLOW a FAIL/WARN scan (owner MFA)


class KillSwitchRequest(BaseModel):
    engaged: bool = Field(description="True = engage the brake, False = release it")
    reason: str = Field(default="", max_length=500, description="Owner's reason, audited")
    totp: str | None = Field(
        default=None, description="Owner TOTP — required only to RELEASE (engaged=False)"
    )


# ------------------------------------------------------------------
# Router factory
# ------------------------------------------------------------------


def create_security_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/security", tags=["security"])

    @router.get("/scans")
    async def list_recent_scans(request: Request, limit: int = 50) -> list[dict]:
        """List recent security scans.

        Fail-soft: returns [] when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_list("list_recent_scans", limit)
        except AgentUnavailable as exc:
            logger.warning(
                "hermes.security.scans.unavailable",
                extra={"reason": str(exc)},
            )
            return []

    @router.get("/policy")
    async def get_security_policy(request: Request) -> dict:
        """Get the current security policy.

        Fail-soft: returns empty dict when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_dict("get_security_policy")
        except AgentUnavailable as exc:
            logger.warning(
                "hermes.security.policy.unavailable",
                extra={"reason": str(exc)},
            )
            return {}

    @router.get("/audit/head")
    async def get_audit_chain_head(request: Request) -> dict:
        """Get the audit chain head (integrity status and head hash).

        Fail-soft: returns degraded status when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_dict("get_audit_chain_head")
        except AgentUnavailable as exc:
            logger.warning(
                "hermes.security.audit_head.unavailable",
                extra={"reason": str(exc)},
            )
            return {"integrity": "unknown", "head_hash": ""}

    @router.post("/scans/install", status_code=202)
    async def scan_install(request: Request, body: ScanInstallRequest) -> dict:
        """Run a pre-install security scan.

        Returns:
          {
            scan_id: str,
            verdict: "PASS" | "WARN" | "FAIL",
            score: int,
            engine: "trivy" | "heuristic",
            engine_label: str,             # human-readable: what type of scan ran
            requires_owner_approval: bool, # true when verdict != "PASS"
            risks: [{category, severity, message, evidence_ref}]
          }

        When engine="heuristic", the scan ran without a full CVE database and the
        verdict may be conservative.  If requires_owner_approval=true, the owner
        can approve via POST /api/v1/security/decisions.

        Fail-hard 503 on daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_mutator("scan_install", body.kind, body.identifier)
        except AgentUnavailable as exc:
            _raise_503(exc, "scan_install")

    @router.post("/decisions", status_code=201)
    async def record_install_decision(
        request: Request, body: RecordInstallDecisionRequest
    ) -> dict:
        """Record an operator decision on a security scan (approve/allow or deny).

        ALLOW/APPROVE on a non-PASS scan is a SOVEREIGN override → requires the owner's
        TOTP (audited). Plain deny needs none. fail-hard on daemon unavailable.
        """
        is_override = body.decision.strip().lower() in _OVERRIDE_DECISIONS
        if is_override:
            require_owner_mfa(
                MfaStore(),
                body.totp or "",
                action="permitir una instalación que el antivirus marcó (queda auditado)",
            )
        proxy = request.app.state.dbus_proxy
        try:
            result = await proxy.call_mutator(
                "record_install_decision",
                body.scan_id,
                body.decision,
                body.identifier,
                body.kind,
                body.score,
                body.verdict,
                body.risks_json,
            )
        except AgentUnavailable as exc:
            _raise_503(exc, "record_install_decision")

        # A skill override just spent the owner's TOTP right here — mint a
        # short-lived, single-use re-auth grant so the follow-up
        # POST /skills/hub/install force=True call (SkillsView's
        # handleScanApprove → doInstallSkill) doesn't need a SECOND code —
        # which would fail anyway, TOTP is single-use (totp_replayed).
        if is_override and body.kind == "skill" and body.identifier:
            grant = issue_reauth_grant(identifier=body.identifier, action="install_hub_skill")
            if isinstance(result, dict):
                result = {**result, "reauth_grant": grant}
        return result

    @router.get("/kill-switch")
    async def get_kill_switch(request: Request) -> dict:
        """Estado del freno de emergencia. Fail-soft: {engaged: false, ...} si
        el daemon no está disponible (nunca 503 en una lectura de estado)."""
        proxy = request.app.state.dbus_proxy
        try:
            status = await proxy.call_dict("get_kill_switch_status")
        except AgentUnavailable:
            return {"engaged": False, "reason": None, "changed_by": None, "changed_at": None}
        return status or {"engaged": False, "reason": None, "changed_by": None, "changed_at": None}

    @router.post("/kill-switch", status_code=200)
    async def set_kill_switch(request: Request, body: KillSwitchRequest) -> dict:
        """Engage/release the emergency brake.

        Engaging (engaged=true) needs NOTHING beyond the operator bearer — it
        is a brake, one click. Releasing (engaged=false) is a sovereign action
        that requires the owner's TOTP, same require_owner_mfa gate as every
        other posture change (/security/decisions, /egress/mode).
        """
        proxy = request.app.state.dbus_proxy
        if body.engaged:
            try:
                await proxy.call_bool("pause", body.reason)
            except AgentUnavailable as exc:
                _raise_503(exc, "kill_switch_engage")
            return {"ok": True, "engaged": True}

        require_owner_mfa(
            MfaStore(),
            body.totp or "",
            action="liberar el freno de emergencia",
        )
        try:
            await proxy.call_bool("resume")
        except AgentUnavailable as exc:
            _raise_503(exc, "kill_switch_release")
        return {"ok": True, "engaged": False}

    return router


def _raise_503(exc: AgentUnavailable, operation: str) -> None:
    logger.warning(
        "hermes.security.mutator_unavailable",
        extra={"operation": operation, "reason": str(exc)},
    )
    raise HTTPException(
        status_code=503,
        detail={
            "code": "agent_unavailable",
            "message": "El agente no está disponible. Comprueba que hermes-runtime está activo.",
        },
    ) from exc
