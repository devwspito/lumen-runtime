"""Gate único de MFA del dueño para cambios de postura de seguridad (web).

Antes había DOS helpers `_require_owner_mfa` casi idénticos (egress_api +
policies_api), cada uno verificando TOTP en su propio endpoint. Esto los colapsa
en una sola superficie de enforcement (lección del red-team 2026-06-19 finding 3:
el enforcement debe ser estructural, no replicable por endpoint).

Modelo TOTP-only (decisión del dueño 2026-06-24). Fail-closed: sin enrolar o
código malo → rechaza. El agente enjaulado no puede acuñar el TOTP (secreto 0600
solo-dueño), así que no puede abrir su propia jaula.
"""
from __future__ import annotations

import logging
import secrets
import time

from fastapi import HTTPException

from hermes.shell_server.security.mfa import MfaStore, ProtectionLevel

logger = logging.getLogger("hermes.shell_server.security.owner_mfa_gate")

# ---------------------------------------------------------------------------
# Re-auth grant — ONE owner TOTP prompt covers a chained sovereign override
# ---------------------------------------------------------------------------
#
# Some flows spend the owner's TOTP once (e.g. POST /security/decisions
# approving a FAIL scan verdict) and then need a SECOND override endpoint
# (e.g. POST /skills/hub/install force=True) in the same breath. Re-asking for
# a fresh code there is a dead end: TOTP is single-use (MfaStore.verify's
# `totp_last_counter` high-water mark), so re-sending the just-spent code
# always fails `totp_replayed`.
#
# This mirrors that replay-guard pattern instead of adding a second MFA path:
# on a successful require_owner_mfa, the caller may mint a short-lived,
# single-use grant bound to the exact (identifier, action) pair, handed back
# to the frontend and presented on the follow-up call as a header (never in
# the body, so it never gets logged as request payload). Stored in-process —
# the shell-server runs a single uvicorn worker (main.py), no cross-process
# fan-out to worry about.
_GRANT_TTL_SECONDS = 120
_reauth_grants: dict[str, tuple[str, str, float]] = {}  # grant -> (identifier, action, expires_at)


def _prune_expired_grants(*, now: float) -> None:
    expired = [g for g, (_, _, exp) in _reauth_grants.items() if exp < now]
    for g in expired:
        del _reauth_grants[g]


def issue_reauth_grant(*, identifier: str, action: str) -> str:
    """Mint a single-use re-auth grant after a fresh TOTP verify just passed.

    Bound to `identifier`/`action` — unusable for any other override target.
    """
    now = time.time()
    _prune_expired_grants(now=now)
    grant = secrets.token_urlsafe(32)
    _reauth_grants[grant] = (identifier, action, now + _GRANT_TTL_SECONDS)
    return grant


def require_owner_mfa_or_grant(
    mfa_store: MfaStore,
    totp: str,
    grant: str | None,
    *,
    identifier: str,
    action: str,
) -> None:
    """Accept EITHER a still-valid re-auth grant OR a fresh owner TOTP.

    A grant is consumed (popped) on first presentation, valid or not, so it
    can never be replayed — even a failed identifier/action mismatch burns it.
    No grant presented → falls through to the normal `require_owner_mfa` bar
    (unchanged 403/401 behaviour for a direct TOTP call).
    """
    if not grant:
        require_owner_mfa(mfa_store, totp, action=action)
        return

    now = time.time()
    entry = _reauth_grants.pop(grant, None)
    _prune_expired_grants(now=now)
    if entry is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_reauth_grant",
                "message": f"{action[:1].upper()}{action[1:]} exige tu código MFA.",
            },
        )
    got_identifier, got_action, expires_at = entry
    if got_identifier != identifier or got_action != action or expires_at < now:
        logger.warning(
            "hermes.mfa.reauth_grant_denied action=%r identifier=%r", action, identifier
        )
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_reauth_grant",
                "message": f"{action[:1].upper()}{action[1:]} exige tu código MFA.",
            },
        )


def require_owner_mfa(mfa_store: MfaStore, totp: str, *, action: str) -> None:
    """Exige el TOTP del dueño para una acción de postura de seguridad.

    `action` es la etiqueta humana de la acción (p.ej. "cambiar el modo de red")
    que se interpola en el mensaje de error. Lanza HTTPException 403 (sin enrolar)
    o 401 (código inválido).
    """
    if not mfa_store.is_enrolled():
        raise HTTPException(
            status_code=403,
            detail={
                "code": "mfa_not_enrolled",
                "message": f"Configura el MFA antes de {action}.",
            },
        )
    ok, reason = mfa_store.verify(level=ProtectionLevel.MFA, totp=totp or "")
    if not ok:
        logger.warning(
            "hermes.mfa.owner_gate_denied action=%r reason=%s", action, reason
        )
        raise HTTPException(
            status_code=401,
            detail={
                "code": reason,
                "message": f"{action[:1].upper()}{action[1:]} exige tu código MFA.",
            },
        )


def require_owner_mfa_if_enrolled(mfa_store: MfaStore, totp: str, *, action: str) -> None:
    """Gate for a SOVEREIGN switch that governs its own enforcement (the
    `mfa_on_dangers` toggle itself) — must stay protected independent of the
    state it controls, but must never lock a fresh install out of its own
    switch before the owner has anything to verify against.

    Enrolled → same bar as require_owner_mfa (401 on missing/bad code).
    Not enrolled → no factor exists to check, so this is a no-op (proceeds) —
    unlike require_owner_mfa, this NEVER raises 403 mfa_not_enrolled here.
    specs/025-safent-repaso SEG-15: the toggle used to sit behind the plain
    require_owner_mfa gate, which the caller ALSO used (unconditionally) for
    every other policy mutation; once the owner turned mfa_on_dangers off,
    the UI stopped prompting for TOTP anywhere (correctly, for non-sovereign
    decisions) but kept sending totp="" to this endpoint too — 401 forever,
    with no way to turn the switch back on from the UI.
    """
    if not mfa_store.is_enrolled():
        return
    ok, reason = mfa_store.verify(level=ProtectionLevel.MFA, totp=totp or "")
    if not ok:
        logger.warning(
            "hermes.mfa.owner_gate_denied action=%r reason=%s", action, reason
        )
        raise HTTPException(
            status_code=401,
            detail={
                "code": reason,
                "message": f"{action[:1].upper()}{action[1:]} exige tu código MFA.",
            },
        )
