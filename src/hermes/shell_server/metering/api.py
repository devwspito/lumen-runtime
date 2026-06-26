"""Metering REST API — usage metrics (tokens/cost) per cycle, agent, conversation.

Endpoints:
  GET /api/v1/usage/summary?period=30d           — aggregated totals + top models
  GET /api/v1/usage/by-agent?period=30d          — per-agent breakdown
  GET /api/v1/usage/timeseries?period=30d        — daily time series
  GET /api/v1/chat/conversations/{id}/usage      — conversation-level detail

All endpoints are fail-soft: errors return {available: false, ...} with empty
arrays — never 500. Arrays are always present (never null) to prevent
undefined.length crashes in the frontend.

period values: "7d" | "30d" | "mtd"  (default: "30d")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from hermes.shell_server.metering.usage_repo import SQLiteUsageRepository

logger = logging.getLogger("hermes.shell_server.metering")

_DB_PATH = Path(
    os.environ.get("HERMES_SHELL_DB", "/var/lib/hermes/shell-state.db")
)

_VALID_PERIODS = frozenset({"7d", "30d", "mtd"})
_DEFAULT_PERIOD = "30d"


def _repo() -> SQLiteUsageRepository:
    return SQLiteUsageRepository(db_path=_DB_PATH)


def _coerce_period(period: str) -> str:
    return period if period in _VALID_PERIODS else _DEFAULT_PERIOD


def create_usage_router() -> APIRouter:
    router = APIRouter(tags=["usage"])

    @router.get("/api/v1/usage/summary")
    async def usage_summary(period: str = _DEFAULT_PERIOD) -> dict[str, Any]:
        """Aggregated cost/token summary for the requested period."""
        try:
            return _repo().summary(period=_coerce_period(period))
        except Exception as exc:  # noqa: BLE001
            logger.warning("hermes.metering.summary_failed: %s", exc)
            return {
                "available": False,
                "period": period,
                "currency": "USD",
                "total_cost_usd": 0.0,
                "projected_cost_usd": 0.0,
                "total_tokens": 0,
                "cycles": 0,
                "failures": 0,
                "self_hosted_cycles": 0,
                "top_models": [],
            }

    @router.get("/api/v1/usage/by-agent")
    async def usage_by_agent(period: str = _DEFAULT_PERIOD) -> dict[str, Any]:
        """Cost and usage breakdown by agent for the requested period."""
        try:
            return _repo().by_agent(period=_coerce_period(period))
        except Exception as exc:  # noqa: BLE001
            logger.warning("hermes.metering.by_agent_failed: %s", exc)
            return {"available": False, "agents": []}

    @router.get("/api/v1/usage/timeseries")
    async def usage_timeseries(
        period: str = _DEFAULT_PERIOD,
        dimension: str = "cost",
    ) -> dict[str, Any]:
        """Daily time series of cost/tokens/cycles for the requested period."""
        try:
            return _repo().timeseries(
                period=_coerce_period(period), dimension=dimension
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("hermes.metering.timeseries_failed: %s", exc)
            return {"available": False, "points": []}

    @router.get("/api/v1/chat/conversations/{conversation_id}/usage")
    async def conversation_usage(conversation_id: str) -> dict[str, Any]:
        """Token and cost detail for all cycles in a specific conversation."""
        try:
            return _repo().conversation_usage(conversation_id=conversation_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hermes.metering.conversation_usage_failed conv=%s: %s",
                conversation_id, exc,
            )
            return {
                "available": False,
                "conversation_id": conversation_id,
                "cost_usd": 0.0,
                "total_tokens": 0,
                "cycles": [],
            }

    return router
