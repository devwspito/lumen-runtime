"""Agent roster endpoint — GET /api/v1/agents/roster.

Devuelve el equipo de agentes agrupado en departamentos, TODO desde el registro de
agentes del daemon (agentes reales, no un catálogo externo):
  • "cerebro"      — el agente default (is_default=True), el que orquesta.
  • Factory        — el roster de especialistas sembrado (default_roster), por departamento.
  • Custom depts   — agentes custom con un department explícito.
  • "mis-agentes"  — agentes custom sin department.

No hay catálogo externo ni harness: el equipo ES el registro real, ejecutado por el
Cerebro vía delegación nativa. Read-only, sin auth (misma postura que GET /api/v1/agents).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from hermes.agents.domain.default_roster import DEPARTMENTS

logger = logging.getLogger("hermes.shell_server.cowork.roster_api")


def _agent_shape(a: dict[str, Any]) -> dict[str, Any]:
    dept = a.get("department")
    is_factory = bool(dept) and dept in DEPARTMENTS
    return {
        "id": a.get("agent_id", ""),
        "name": a.get("name", ""),
        "description": a.get("primary_mission", ""),
        "department": dept,
        "is_default": bool(a.get("is_default", False)),
        "color": a.get("color") or None,
        "source": "factory" if is_factory else "custom",
    }


def _build_departments(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa los agentes del registro en departamentos para el Office."""
    cerebro: list[dict[str, Any]] = []
    by_dept: dict[str, list[dict[str, Any]]] = {}
    misc: list[dict[str, Any]] = []

    for a in agents:
        shape = _agent_shape(a)
        if a.get("is_default"):
            shape["department"] = "cerebro"
            cerebro.append(shape)
            continue
        dept = (a.get("department") or "").strip()
        if dept:
            by_dept.setdefault(dept, []).append(shape)
        else:
            misc.append(shape)

    departments: list[dict[str, Any]] = []

    # Cerebro siempre primero.
    if cerebro:
        departments.append(
            {"id": "cerebro", "name": "Cerebro", "kind": "cerebro", "agents": cerebro}
        )

    # Departamentos de fábrica primero, en el orden de DEPARTMENTS.
    for slug, (label, _color) in DEPARTMENTS.items():
        bucket = by_dept.pop(slug, None)
        if bucket:
            departments.append(
                {"id": slug, "name": label, "kind": "factory", "agents": bucket}
            )

    # Departamentos custom del usuario (alfabético).
    for slug in sorted(by_dept):
        departments.append(
            {
                "id": f"custom:{slug}",
                "name": slug.replace("-", " ").replace("_", " ").title(),
                "kind": "custom",
                "agents": by_dept[slug],
            }
        )

    # Agentes custom sin departamento.
    if misc:
        departments.append(
            {"id": "mis-agentes", "name": "Mis agentes", "kind": "custom", "agents": misc}
        )

    return departments


def create_roster_router() -> APIRouter:
    """Return the APIRouter for GET /api/v1/agents/roster."""
    router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

    @router.get("/roster")
    async def get_agent_roster(request: Request) -> dict[str, Any]:
        """Equipo de agentes agrupado en departamentos (todo del registro real).

        Fail-soft: si el daemon no responde, devuelve departamentos vacíos. Nunca 500.
        """
        proxy = request.app.state.dbus_proxy
        try:
            raw_agents: list[dict] = await proxy.call_list("list_agents")
        except Exception:  # noqa: BLE001
            raw_agents = []

        departments = _build_departments(raw_agents)
        logger.debug("hermes.roster.built departments=%d", len(departments))
        return {"departments": departments}

    return router
