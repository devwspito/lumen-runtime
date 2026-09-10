"""system_update — is there a new version, from the UI (no terminal).

GET reports the current version, the best-effort latest published version,
and (T005) the signed runtime-manifest.json's verified digests, so the UI
can show an honest "update available" hint. The WRITE side — requesting an
update or uninstall, and every other host action the UI can trigger — moved
to `hermes.shell_server.install_requests` (T006): this module now only
answers "what version, and is one already in flight", via
`install_requests.is_verb_live`, the single source of truth for "is there a
live request for this verb".
"""

from __future__ import annotations

import logging
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import hermes
from hermes.shell_server.install_requests import is_verb_live
from hermes.shell_server.runtime_manifest import (
    current_arch_key,
    fetch_verified_manifest,
    pieces_for_arch,
)

logger = logging.getLogger("hermes.shell_server.system_update")

# Source of truth for "what's the latest version" — the repo VERSION file on main.
_LATEST_URL = os.environ.get(
    "SAFENT_VERSION_URL",
    "https://raw.githubusercontent.com/devwspito/safent-runtime/main/VERSION",
)

# The running engine's OWN build identity — baked at image build time
# (ops/container/Containerfile: `echo "${GIT_SHA}" > /usr/share/hermes/build`,
# the same value as the `org.opencontainers.image.revision` label). This is
# the closest honest answer to VersionSet.engine obtainable FROM INSIDE the
# container: a content digest would need host-side `podman inspect`, which
# this daemon-side route cannot reach — contracts/update.md §3's "dos
# fuentes, una verdad" names the desktop app (host-side) as the other one.
_ENGINE_BUILD_FILE = Path("/usr/share/hermes/build")


def _current_engine_build() -> str:
    try:
        return _ENGINE_BUILD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _parse(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(v).strip().lstrip("v").split("."):
        num = "".join(c for c in chunk if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _fetch_latest() -> str | None:
    try:
        with urllib.request.urlopen(_LATEST_URL, timeout=5) as r:
            return r.read().decode("utf-8").strip() or None
    except Exception:  # noqa: BLE001 — network is best-effort; UI still works without it
        return None


def create_system_update_router() -> APIRouter:
    from hermes.shell_server.cowork.live_view_support import verify_token  # noqa: PLC0415

    router = APIRouter()

    def _auth(request: Request) -> None:
        expected = getattr(request.app.state, "shell_webui_token", "")
        auth = request.headers.get("authorization", "")
        tok = auth[7:] if auth[:7].lower() == "bearer " else ""
        if not verify_token(tok, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @router.get("/api/v1/system/update")
    async def system_update_status(request: Request) -> dict[str, object]:
        _auth(request)
        current = str(getattr(hermes, "__version__", "0"))
        latest = _fetch_latest()
        manifest = fetch_verified_manifest()
        # Fail-closed (Constitution Principle IV): a plain-text VERSION bump
        # is no longer enough on its own — the signed manifest must ALSO
        # verify, or no button is shown, regardless of what the unsigned
        # VERSION file claims (contracts/update.md §2.3).
        available = latest is not None and _parse(latest) > _parse(current) and manifest is not None
        arch_key = current_arch_key()
        engine_digest = manifest.engine.get(arch_key) if manifest else None
        companion_digest = None
        if manifest:
            companion_digest = manifest.companion.get("safent-ads", {}).get(arch_key)
        pieces = pieces_for_arch(manifest, arch_key) if manifest else []

        # UPD-N2 (specs/025-safent-repaso matriz-final-39eeb8e): the contract
        # (update.md §3) says this route "se amplía con los mismos campos"
        # del objeto `window.__safentUpdate` — available/current (VersionSet)
        # /to/checked_at — CONSERVING current_version/latest_version/
        # update_available above. `available` mirrors `update_available`
        # exactly (both are the same boolean, spelled for either shape's
        # readers); `to` is only populated when there is something to move
        # to (an empty plan → null, per §3's own "plan vacío -> null" rule).
        current_set = {"app": current, "engine": _current_engine_build(), "companion": None}
        to_set = (
            {"app": latest, "engine": engine_digest, "companion": companion_digest}
            if available
            else None
        )
        return {
            "current_version": current,
            "latest_version": latest,
            "update_available": available,
            "updating": is_verb_live("update_system"),
            "engine_digest": engine_digest,
            "companion_digest": companion_digest,
            "pieces": pieces,
            "available": available,
            "current": current_set,
            "to": to_set,
            "checked_at": datetime.now(tz=UTC).isoformat(),
        }

    return router
