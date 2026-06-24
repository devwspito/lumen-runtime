"""Notifications REST API — bell surface for task/chat completion events.

Endpoints:
  GET  /api/v1/notifications              list recent notifications (newest first)
  GET  /api/v1/notifications/unread-count return the unread count
  POST /api/v1/notifications/{id}/read    mark one notification as read
  POST /api/v1/notifications/read-all     mark all notifications as read

All reads are fail-soft (return empty / 0 when daemon unavailable).
Mutators are fail-soft too — a mark-read failure must never block the UI.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request

from hermes.tasks.control_plane.domain.ports import AgentUnavailable

logger = logging.getLogger("hermes.shell_server.cowork.notifications_api")


def create_notifications_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

    @router.get("")
    async def list_notifications(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        unread_only: bool = Query(False),
    ) -> list[dict]:
        """List recent notifications newest-first.

        Returns [{id, kind, title, body, status, conversation_id,
                  created_at, read}].
        Fail-soft: returns [] when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_list(
                "list_notifications", limit, unread_only
            )
        except AgentUnavailable as exc:
            logger.warning(
                "hermes.notifications.list_unavailable",
                extra={"reason": str(exc)},
            )
            return []

    @router.get("/unread-count")
    async def get_unread_count(request: Request) -> dict:
        """Return the count of unread notifications.

        Returns {count: int}.
        Fail-soft: returns {count: 0} when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            raw = await proxy._call("get_notification_unread_count")  # noqa: SLF001
            return {"count": int(raw) if raw is not None else 0}
        except Exception:  # noqa: BLE001 — AgentUnavailable or D-Bus error
            return {"count": 0}

    @router.post("/{notification_id}/read")
    async def mark_notification_read(
        request: Request, notification_id: str
    ) -> dict:
        """Mark one notification as read.

        Returns {ok: bool, updated: bool}.
        Fail-soft: returns {ok: true} even when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_dict(
                "mark_notification_read", notification_id
            )
        except AgentUnavailable as exc:
            logger.warning(
                "hermes.notifications.mark_read_unavailable",
                extra={"reason": str(exc)},
            )
            return {"ok": True, "updated": False}

    @router.post("/read-all")
    async def mark_all_read(request: Request) -> dict:
        """Mark all unread notifications as read.

        Returns {ok: bool, count: int}.
        Fail-soft: returns {ok: true, count: 0} when daemon unavailable.
        """
        proxy = request.app.state.dbus_proxy
        try:
            return await proxy.call_dict("mark_all_notifications_read")
        except AgentUnavailable as exc:
            logger.warning(
                "hermes.notifications.mark_all_read_unavailable",
                extra={"reason": str(exc)},
            )
            return {"ok": True, "count": 0}

    return router
