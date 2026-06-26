"""GET /api/v1/runtime/agent-stats — live agent state + today's usage.

Merges three read-only sources:
  1. D-Bus get_runtime_status  → which agents are currently working (activity[]).
  2. SqliteAgentRegistry       → name / department / color for every known agent.
  3. SQLiteUsageRepository     → tokens / cost_usd / tasks for today (UTC).

Contract (fail-soft, arrays always present — never 500):
  {
    "available": true,
    "agents": [
      {
        "agent_id": "...",
        "name":     "...",
        "department": "...",
        "color":    "...",
        "state":    "idle" | "working",
        "active_task_count": 0,
        "today": {"tokens": 0, "cost_usd": 0.0, "tasks": 0},
        "health": "ok" | "degraded" | "unknown"
      }
    ]
  }

On any error: {"available": false, "agents": []}.

Design notes:
- `state` = "working" when the agent appears in activity[] or equals active_agent_id.
- `health` = "degraded" when today's tasks > 0 but cycles include failures (not
  exposed here — kept as "ok" / "unknown" for simplicity; "degraded" is reserved
  for future signal).
- The registry list_agents() respects the roster-on/off toggle (same as the roster
  endpoint) so hidden specialists do not clutter the stats view.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from hermes.shell_server.metering.usage_repo import SQLiteUsageRepository

logger = logging.getLogger("hermes.shell_server.metering.agent_stats")

_DB_PATH = Path(
    os.environ.get("HERMES_SHELL_DB", "/var/lib/hermes/shell-state.db")
)

_EMPTY_TODAY: dict[str, Any] = {"tokens": 0, "cost_usd": 0.0, "tasks": 0}


def _active_agent_ids(runtime_status: dict[str, Any]) -> frozenset[str]:
    """Extract the set of agent_ids currently active from a runtime status dict."""
    active: set[str] = set()

    active_agent_id = runtime_status.get("active_agent_id")
    if active_agent_id:
        active.add(str(active_agent_id))

    for entry in runtime_status.get("activity", []) or []:
        aid = (entry or {}).get("agent_id")
        if aid:
            active.add(str(aid))

    return frozenset(active)


def _agent_stat(
    agent: dict[str, Any],
    *,
    active_ids: frozenset[str],
    today_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    agent_id = agent.get("agent_id", "")
    is_working = agent_id in active_ids
    today = dict(today_map.get(agent_id, _EMPTY_TODAY))

    return {
        "agent_id": agent_id,
        "name": agent.get("name", ""),
        "department": agent.get("department") or "",
        "color": agent.get("color") or "",
        "state": "working" if is_working else "idle",
        "active_task_count": 1 if is_working else 0,
        "today": today,
        "health": "ok" if today["tasks"] > 0 else "unknown",
    }


def create_agent_stats_router() -> APIRouter:
    """Return the APIRouter for GET /api/v1/runtime/agent-stats."""
    router = APIRouter(tags=["runtime"])

    @router.get("/api/v1/runtime/agent-stats")
    async def agent_stats(request: Request) -> dict[str, Any]:
        """Live agent floor status + today's token/cost usage per agent.

        Fail-soft: any error returns {available: false, agents: []} so the
        frontend never receives undefined where it expects an array.
        """
        try:
            return await _build_agent_stats(request)
        except Exception:  # noqa: BLE001
            logger.exception("hermes.agent_stats.build_failed")
            return {"available": False, "agents": []}

    return router


async def _build_agent_stats(request: Request) -> dict[str, Any]:
    proxy = request.app.state.dbus_proxy

    runtime_status: dict[str, Any] = {}
    try:
        runtime_status = await proxy.call_dict("get_runtime_status")
    except Exception:  # noqa: BLE001
        logger.warning("hermes.agent_stats.dbus_unavailable — proceeding without live state")

    active_ids = _active_agent_ids(runtime_status)

    raw_agents = await _list_agents_safe(proxy)

    try:
        today_map = _fetch_today_map()
    except Exception:  # noqa: BLE001
        logger.warning("hermes.agent_stats.fetch_today_failed — today usage unavailable")
        today_map = {}

    agents = [
        _agent_stat(a, active_ids=active_ids, today_map=today_map)
        for a in raw_agents
    ]

    available = bool(runtime_status)
    return {"available": available, "agents": agents}


async def _list_agents_safe(proxy: Any) -> list[dict[str, Any]]:
    try:
        return await proxy.call_list("list_agents")
    except Exception:  # noqa: BLE001
        logger.warning("hermes.agent_stats.list_agents_failed — returning empty roster")
        return []


def _fetch_today_map() -> dict[str, dict[str, Any]]:
    try:
        repo = SQLiteUsageRepository(db_path=_DB_PATH)
        return repo.today_by_agent()
    except Exception:  # noqa: BLE001
        logger.warning("hermes.agent_stats.usage_repo_failed — today usage unavailable")
        return {}
