"""Scheduled tasks REST API — D-Bus surface for task scheduler management.

GET configured and recent tasks are already exposed by main.py at:
  GET /api/v1/tasks/configured
  GET /api/v1/tasks/recent

This module adds the detail and mutator surface:
  GET    /api/v1/tasks/scheduled/{id}          task detail (instruction, agent, risk, cron, next_run)
  POST   /api/v1/tasks/scheduled               create a scheduled task
  PUT    /api/v1/tasks/scheduled/{id}          update a scheduled task
  DELETE /api/v1/tasks/scheduled/{id}          delete a scheduled task
  POST   /api/v1/tasks/scheduled/{id}/enabled  toggle enabled state

Security:
  - Mutators carry a signed OperatorToken (DbusRuntimeProxy.call_mutator).
  - fail-hard 503 for mutators (CTRL-P1-11).
  - GET detail is read-only (fail-soft: 404 when not found).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hermes.tasks.control_plane.domain.ports import AgentUnavailable

logger = logging.getLogger("hermes.shell_server.cowork.tasks_api")


# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------


class CreateScheduledTaskRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    cron: str = Field(min_length=1, description="cron expression, e.g. '0 9 * * 1-5'")
    instruction: str = Field(min_length=1, max_length=2000)
    target_agent_id: str | None = None
    risk_ceiling: str = Field(default="medium")
    one_shot: bool = False


class UpdateScheduledTaskRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    cron: str = Field(min_length=1, description="cron expression, e.g. '0 9 * * 1-5'")
    instruction: str = Field(min_length=1, max_length=2000)
    target_agent_id: str | None = None
    risk_ceiling: str = Field(default="low")


class SetEnabledRequest(BaseModel):
    enabled: bool


# ------------------------------------------------------------------
# Router factory
# ------------------------------------------------------------------


def create_tasks_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tasks/scheduled", tags=["tasks"])

    @router.get("/{trigger_id}")
    async def get_scheduled_task(request: Request, trigger_id: str) -> dict:
        """Retrieve detail for one scheduled task.

        Returns {trigger_id, label, cron, task_instruction, target_agent_id,
        risk_ceiling, recurrence_human, next_run_at, last_run_at, last_status,
        one_shot, title}. 404 when the trigger does not exist or is disabled.
        """
        proxy = request.app.state.dbus_proxy
        try:
            result = await proxy.call_dict("get_scheduled_task", trigger_id)
        except AgentUnavailable as exc:
            _raise_503(exc, "get_scheduled_task")
        if not result:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": f"Scheduled task {trigger_id!r} not found."},
            )
        return result

    @router.post("", status_code=201)
    async def create_scheduled_task(
        request: Request, body: CreateScheduledTaskRequest
    ) -> dict:
        """Create a new scheduled task trigger."""
        proxy = request.app.state.dbus_proxy
        # Daemon contract (create_scheduled_task): {title, task_instruction, cron,
        # risk_ceiling ∈ {low,high}, target_agent_id?}. Map the request's
        # label/instruction onto title/task_instruction and normalise the ceiling
        # (the daemon rejects "medium"); otherwise the row is never written and the
        # task silently never appears in the dashboard.
        ceiling = body.risk_ceiling.lower() if body.risk_ceiling else "low"
        if ceiling not in ("low", "high"):
            ceiling = "high" if ceiling in ("critical", "severe") else "low"
        draft = {
            "title": body.label,
            "task_instruction": body.instruction,
            "cron": body.cron,
            "risk_ceiling": ceiling,
            "one_shot": body.one_shot,
        }
        if body.target_agent_id:
            draft["target_agent_id"] = body.target_agent_id
        try:
            return await proxy.call_mutator("create_scheduled_task", json.dumps(draft))
        except AgentUnavailable as exc:
            _raise_503(exc, "create_scheduled_task")

    @router.put("/{trigger_id}")
    async def update_scheduled_task(
        request: Request, trigger_id: str, body: UpdateScheduledTaskRequest
    ) -> dict:
        """Update mutable fields of a scheduled task.

        Returns the updated task dict on success.
        403 if not authorized; 404 if trigger not found or disabled.
        """
        import json as _json  # noqa: PLC0415

        proxy = request.app.state.dbus_proxy
        ceiling = body.risk_ceiling.lower() if body.risk_ceiling else "low"
        if ceiling not in ("low", "high"):
            ceiling = "high" if ceiling in ("critical", "severe") else "low"
        draft = {
            "label": body.label,
            "instruction": body.instruction,
            "cron": body.cron,
            "risk_ceiling": ceiling,
        }
        if body.target_agent_id:
            draft["target_agent_id"] = body.target_agent_id
        try:
            return await proxy.call_mutator(
                "update_scheduled_task", trigger_id, _json.dumps(draft)
            )
        except AgentUnavailable as exc:
            _raise_503(exc, "update_scheduled_task")

    @router.delete("/{trigger_id}", status_code=204)
    async def delete_scheduled_task(request: Request, trigger_id: str) -> None:
        """Delete a scheduled task trigger."""
        proxy = request.app.state.dbus_proxy
        try:
            await proxy.call_mutator("delete_scheduled_task", trigger_id)
        except AgentUnavailable as exc:
            _raise_503(exc, "delete_scheduled_task")

    @router.post("/{trigger_id}/enabled")
    async def set_task_enabled(
        request: Request, trigger_id: str, body: SetEnabledRequest
    ) -> dict:
        """Enable or disable a scheduled task trigger."""
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_mutator(
                "set_scheduled_task_enabled", trigger_id, body.enabled
            )
        except AgentUnavailable as exc:
            _raise_503(exc, "set_scheduled_task_enabled")

    return router


def _raise_503(exc: AgentUnavailable, operation: str) -> None:
    logger.warning(
        "hermes.tasks.mutator_unavailable",
        extra={"operation": operation, "reason": str(exc)},
    )
    raise HTTPException(
        status_code=503,
        detail={
            "code": "agent_unavailable",
            "message": "El agente no está disponible. Comprueba que hermes-runtime está activo.",
        },
    ) from exc
