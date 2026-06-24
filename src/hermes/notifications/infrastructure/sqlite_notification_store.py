"""SqliteNotificationStore — durable notification store in shell-state.db.

Notifications are written by the daemon (at task/chat completion points) and
read by the shell-server via D-Bus.  Both processes share the same SQLite WAL
file (/var/lib/hermes/shell-state.db) — the same pattern as `agent_tasks`,
`agent_runtime_state`, and the conversation mirror.

Schema (P5 migration — EXPAND only, no table recreation):
  CREATE TABLE IF NOT EXISTS notifications (
      id            TEXT PRIMARY KEY,
      kind          TEXT NOT NULL  -- 'task' | 'chat' | 'system'
      title         TEXT NOT NULL,
      body          TEXT NOT NULL,
      status        TEXT NOT NULL  -- 'ok' | 'error' | 'info'
      conversation_id TEXT,        -- nullable; set on chat notifications
      created_at    TEXT NOT NULL,
      read          INTEGER NOT NULL DEFAULT 0
  )

This module is infrastructure only: no domain types, no FastAPI, no D-Bus.
It is imported by DbusRuntimeServiceWiring (daemon) and never by the shell-server
REST layer directly (the REST layer calls through D-Bus).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

# ── Schema ──────────────────────────────────────────────────────────────────

_DDL_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL
        CHECK (kind IN ('task', 'chat', 'system')),
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL
        CHECK (status IN ('ok', 'error', 'info')),
    conversation_id TEXT,
    created_at      TEXT NOT NULL,
    read            INTEGER NOT NULL DEFAULT 0
        CHECK (read IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_notifications_created_at
    ON notifications (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_unread
    ON notifications (read, created_at DESC)
    WHERE read = 0;
"""

_PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
"""

_MAX_TITLE_LEN = 200
_MAX_BODY_LEN = 1000
_DEFAULT_LIMIT = 100


def ensure_notifications_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema application for the notifications table.

    Called by SqliteNotificationStore on first open.  Safe to call from both
    daemon and shell-server processes — WAL allows concurrent readers.
    """
    conn.executescript(_PRAGMAS)
    conn.executescript(_DDL_NOTIFICATIONS)


# ── Store ────────────────────────────────────────────────────────────────────


class SqliteNotificationStore:
    """Per-install notification store backed by shell-state.db.

    Connection-per-call pattern (same as SqliteWorkQueue / SqliteConsentRepo):
    no persistent connection held; each method opens, operates, closes.

    Thread-safe reads: WAL allows concurrent readers without blocking.
    Writes are serialised by SQLite's busy_timeout=5000ms.
    """

    def __init__(self, *, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            ensure_notifications_schema(conn)

    # ------------------------------------------------------------------
    # Write API (called from daemon at completion points)
    # ------------------------------------------------------------------

    def add(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        status: str,
        conversation_id: str | None = None,
    ) -> str:
        """Insert a new notification. Returns the new notification id.

        Truncates title/body to safe limits so oversized task instructions
        never bloat the bus or the UI.
        """
        _validate_kind(kind)
        _validate_status(status)
        title = title.strip()[:_MAX_TITLE_LEN]
        body = body.strip()[:_MAX_BODY_LEN]
        nid = str(uuid4())
        now = datetime.now(tz=UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notifications
                    (id, kind, title, body, status, conversation_id, created_at, read)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (nid, kind, title, body, status, conversation_id, now),
            )
        logger.info(
            "hermes.notifications.add kind=%s status=%s",
            kind,
            status,
        )
        return nid

    def mark_read(self, notification_id: str) -> bool:
        """Mark one notification as read. Returns True if the row existed."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE notifications SET read = 1 WHERE id = ?",
                (notification_id,),
            )
            return cur.rowcount > 0

    def mark_all_read(self) -> int:
        """Mark all unread notifications as read. Returns the count updated."""
        with self._connect() as conn:
            cur = conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
            return cur.rowcount

    # ------------------------------------------------------------------
    # Read API (called from daemon D-Bus verbs then surfaced via REST)
    # ------------------------------------------------------------------

    def list(
        self,
        *,
        limit: int = _DEFAULT_LIMIT,
        unread_only: bool = False,
    ) -> list[dict]:
        """Return notifications newest-first.

        Each dict: {id, kind, title, body, status, conversation_id,
                    created_at, read (bool)}.
        """
        limit = max(1, min(limit, 500))
        where = "WHERE read = 0" if unread_only else ""
        sql = f"""
            SELECT id, kind, title, body, status, conversation_id, created_at, read
            FROM notifications
            {where}
            ORDER BY created_at DESC
            LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def unread_count(self) -> int:
        """Return the count of unread notifications."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE read = 0"
            ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


def _validate_kind(kind: str) -> None:
    if kind not in ("task", "chat", "system"):
        raise ValueError(f"Invalid notification kind: {kind!r}")


def _validate_status(status: str) -> None:
    if status not in ("ok", "error", "info"):
        raise ValueError(f"Invalid notification status: {status!r}")


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "status": row["status"],
        "conversation_id": row["conversation_id"],
        "created_at": row["created_at"],
        "read": bool(row["read"]),
    }
