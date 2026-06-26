"""SQLiteUsageRepository — persistencia de métricas de uso (tokens/coste).

Schema:
  usage_events  — una fila por ciclo de razonamiento completado.
  usage_daily   — rollup diario por (day, agent_id, model) para consultas rápidas.

Telemetry upload tracking:
  usage_events.uploaded — 0 = pending upload, 1 = already sent to cloud.
  Only aggregate counters (tokens, cost, tasks, failures) are ever uploaded;
  no content, PII, prompts, or URLs leave the instance.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hermes.domain.cycle_output import TokenUsage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
  event_id         TEXT PRIMARY KEY,
  ts               TEXT NOT NULL,
  agent_id         TEXT,
  conversation_id  TEXT,
  task_id          TEXT,
  provider         TEXT,
  model            TEXT NOT NULL,
  prompt_tokens    INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens     INTEGER NOT NULL DEFAULT 0,
  cost_usd         REAL NOT NULL DEFAULT 0.0,
  tool_calls       INTEGER NOT NULL DEFAULT 0,
  latency_ms       INTEGER,
  outcome          TEXT NOT NULL DEFAULT 'completed',
  cost_status      TEXT NOT NULL DEFAULT 'unknown',
  cost_source      TEXT NOT NULL DEFAULT 'none',
  uploaded         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS usage_ts_idx
  ON usage_events (ts DESC);

CREATE INDEX IF NOT EXISTS usage_agent_ts_idx
  ON usage_events (agent_id, ts DESC);

CREATE INDEX IF NOT EXISTS usage_conv_idx
  ON usage_events (conversation_id);

CREATE TABLE IF NOT EXISTS usage_daily (
  day               TEXT NOT NULL,
  agent_id          TEXT,
  model             TEXT,
  cycles            INTEGER NOT NULL DEFAULT 0,
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd          REAL NOT NULL DEFAULT 0.0,
  tool_calls        INTEGER NOT NULL DEFAULT 0,
  failures          INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, agent_id, model)
);
"""

# Idempotent migration: add 'uploaded' column to pre-existing databases.
_MIGRATE_UPLOADED_COLUMN = (
    "ALTER TABLE usage_events ADD COLUMN uploaded INTEGER NOT NULL DEFAULT 0"
)

# Index on 'uploaded' is created AFTER the migration so it is safe on old DBs.
_UPLOADED_INDEX = (
    "CREATE INDEX IF NOT EXISTS usage_uploaded_idx ON usage_events (uploaded, ts)"
)


@dataclass(frozen=True, slots=True)
class UnsentAggregate:
    """Aggregate counters for one (agent_id, day) window — safe to upload."""

    agent_id: str | None
    day: str                   # ISO-8601 date (YYYY-MM-DD)
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    tasks: int                 # total cycles (completed + failed)
    failures: int
    event_ids: tuple[str, ...]  # for mark_uploaded; NOT sent to cloud

_COMPLETED_OUTCOMES = frozenset({"completed", "pending_approval"})


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _period_bounds(period: str) -> tuple[str, int]:
    """Return (start_day_iso, total_days_in_period) for the given period key."""
    now = datetime.now(tz=UTC)
    today = now.date()

    if period == "7d":
        total_days = 7
        start = today.replace(day=today.day - 6) if today.day > 6 else today
        # Simpler: subtract days
        from datetime import timedelta  # noqa: PLC0415
        start = today - timedelta(days=6)
    elif period == "mtd":
        start = today.replace(day=1)
        total_days = today.day
    else:  # default: 30d
        from datetime import timedelta  # noqa: PLC0415
        start = today - timedelta(days=29)
        total_days = 30

    return start.isoformat(), total_days


def _elapsed_days(start_day: str) -> int:
    """Days elapsed from start_day up to and including today."""
    today = datetime.now(tz=UTC).date()
    start = datetime.fromisoformat(start_day).date()
    delta = (today - start).days + 1
    return max(1, delta)


class SQLiteUsageRepository:
    """Persistence de usage_events y rollup usage_daily en SQLite WAL."""

    def __init__(self, *, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._migrate_uploaded_column(conn)
            conn.execute(_UPLOADED_INDEX)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate_uploaded_column(self, conn: sqlite3.Connection) -> None:
        """Idempotent: add 'uploaded' column when upgrading from pre-Fase-5 schema."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage_events)")}
        if "uploaded" not in cols:
            conn.execute(_MIGRATE_UPLOADED_COLUMN)

    def record_cycle(
        self,
        *,
        agent_id: str | None,
        conversation_id: str | None,
        task_id: str | None,
        usage: TokenUsage,
        tool_calls: int,
        latency_ms: int | None,
        outcome: str,
    ) -> None:
        """Inserta un ciclo en usage_events y acumula en usage_daily."""
        ts = _now_iso()
        day = ts[:10]
        event_id = str(uuid4())
        is_failure = int(outcome not in _COMPLETED_OUTCOMES)

        with self._connect() as conn:
            conn.execute("BEGIN")
            conn.execute(
                """
                INSERT INTO usage_events (
                  event_id, ts, agent_id, conversation_id, task_id,
                  provider, model, prompt_tokens, completion_tokens,
                  total_tokens, cost_usd, tool_calls, latency_ms,
                  outcome, cost_status, cost_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, ts,
                    agent_id, conversation_id, task_id,
                    usage.provider or None, usage.model,
                    usage.prompt_tokens, usage.completion_tokens,
                    usage.total_tokens, usage.cost_usd,
                    tool_calls, latency_ms, outcome,
                    usage.cost_status, usage.cost_source,
                ),
            )
            conn.execute(
                """
                INSERT INTO usage_daily (
                  day, agent_id, model, cycles, prompt_tokens,
                  completion_tokens, cost_usd, tool_calls, failures
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(day, agent_id, model) DO UPDATE SET
                  cycles            = cycles + 1,
                  prompt_tokens     = prompt_tokens + excluded.prompt_tokens,
                  completion_tokens = completion_tokens + excluded.completion_tokens,
                  cost_usd          = cost_usd + excluded.cost_usd,
                  tool_calls        = tool_calls + excluded.tool_calls,
                  failures          = failures + excluded.failures
                """,
                (
                    day, agent_id, usage.model,
                    usage.prompt_tokens, usage.completion_tokens,
                    usage.cost_usd, tool_calls, is_failure,
                ),
            )
            conn.execute("COMMIT")

    def summary(self, *, period: str = "30d") -> dict[str, Any]:
        """Resumen agregado del periodo (cost, tokens, cycles, projected)."""
        start_day, total_days = _period_bounds(period)
        elapsed = _elapsed_days(start_day)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT model, SUM(cycles) AS cycles,
                       SUM(prompt_tokens) AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens,
                       SUM(cost_usd) AS cost_usd,
                       SUM(failures) AS failures,
                       SUM(tool_calls) AS tool_calls
                  FROM usage_daily
                 WHERE day >= ?
                 GROUP BY model
                """,
                (start_day,),
            ).fetchall()

        if not rows:
            return _empty_summary(period)

        total_cost = sum(float(r["cost_usd"] or 0) for r in rows)
        total_tokens = sum(
            int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0)
            for r in rows
        )
        total_cycles = sum(int(r["cycles"] or 0) for r in rows)
        total_failures = sum(int(r["failures"] or 0) for r in rows)

        projected = (total_cost / elapsed) * total_days

        self_hosted = _count_self_hosted_cycles(self._connect(), start_day)

        top_models = _build_top_models(rows, total_cost)

        return {
            "available": True,
            "period": period,
            "currency": "USD",
            "total_cost_usd": round(total_cost, 6),
            "projected_cost_usd": round(projected, 6),
            "total_tokens": total_tokens,
            "cycles": total_cycles,
            "failures": total_failures,
            "self_hosted_cycles": self_hosted,
            "top_models": top_models,
        }

    def by_agent(self, *, period: str = "30d") -> dict[str, Any]:
        """Coste y uso desglosados por agente.

        LEFT JOIN con agents (misma DB) para enriquecer con name/department.
        Fail-soft: si la tabla agents no existe aún (DB vacía o test aislado),
        devuelve filas sin enriquecimiento (name vacío, department vacío).
        """
        start_day, _ = _period_bounds(period)

        with self._connect() as conn:
            agents_exists = _table_exists(conn, "agents")
            if agents_exists:
                sql = """
                    SELECT d.agent_id,
                           COALESCE(a.name, '(sin agente)') AS name,
                           COALESCE(a.department, '') AS department,
                           SUM(d.cost_usd) AS cost_usd,
                           SUM(d.prompt_tokens + d.completion_tokens) AS total_tokens,
                           SUM(d.cycles) AS cycles
                      FROM usage_daily d
                      LEFT JOIN agents a ON a.agent_id = d.agent_id
                     WHERE d.day >= ?
                     GROUP BY d.agent_id
                """
            else:
                sql = """
                    SELECT d.agent_id,
                           '(sin agente)' AS name,
                           '' AS department,
                           SUM(d.cost_usd) AS cost_usd,
                           SUM(d.prompt_tokens + d.completion_tokens) AS total_tokens,
                           SUM(d.cycles) AS cycles
                      FROM usage_daily d
                     WHERE d.day >= ?
                     GROUP BY d.agent_id
                """
            rows = conn.execute(sql, (start_day,)).fetchall()

        if not rows:
            return {"available": True, "agents": []}

        total_cost = sum(float(r["cost_usd"] or 0) for r in rows)

        agents = [
            {
                "agent_id": r["agent_id"],
                "name": r["name"] or "(sin agente)",
                "department": r["department"] or "",
                "cost_usd": round(float(r["cost_usd"] or 0), 6),
                "total_tokens": int(r["total_tokens"] or 0),
                "cycles": int(r["cycles"] or 0),
                "share": _safe_share(float(r["cost_usd"] or 0), total_cost),
            }
            for r in rows
        ]

        return {"available": True, "agents": agents}

    def today_by_agent(self) -> dict[str, int | float]:
        """Usage aggregated per agent_id for today (UTC) only.

        Returns a mapping of agent_id → {tokens, cost_usd, tasks} where
        tasks == cycles recorded today.  Agents with no usage today are absent
        from the map; the caller must default to zero for missing entries.

        Fail-soft: returns {} on any error (caller handles).
        """
        today = datetime.now(tz=UTC).date().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_id,
                       SUM(prompt_tokens + completion_tokens) AS total_tokens,
                       SUM(cost_usd) AS cost_usd,
                       SUM(cycles) AS tasks
                  FROM usage_daily
                 WHERE day = ?
                 GROUP BY agent_id
                """,
                (today,),
            ).fetchall()
        return {
            r["agent_id"]: {
                "tokens": int(r["total_tokens"] or 0),
                "cost_usd": round(float(r["cost_usd"] or 0), 6),
                "tasks": int(r["tasks"] or 0),
            }
            for r in rows
            if r["agent_id"] is not None
        }

    def timeseries(self, *, period: str = "30d", dimension: str = "cost") -> dict[str, Any]:
        """Serie temporal diaria (cost/tokens/cycles) en el periodo."""
        start_day, _ = _period_bounds(period)

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT day,
                       SUM(cost_usd) AS cost_usd,
                       SUM(prompt_tokens + completion_tokens) AS tokens,
                       SUM(cycles) AS cycles
                  FROM usage_daily
                 WHERE day >= ?
                 GROUP BY day
                 ORDER BY day ASC
                """,
                (start_day,),
            ).fetchall()

        points = [
            {
                "day": r["day"],
                "cost_usd": round(float(r["cost_usd"] or 0), 6),
                "tokens": int(r["tokens"] or 0),
                "cycles": int(r["cycles"] or 0),
            }
            for r in rows
        ]

        return {"available": True, "points": points}

    def conversation_usage(self, *, conversation_id: str) -> dict[str, Any]:
        """Uso acumulado y detalle de ciclos de una conversación."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, model, prompt_tokens, completion_tokens,
                       cost_usd, tool_calls, latency_ms, outcome
                  FROM usage_events
                 WHERE conversation_id = ?
                 ORDER BY ts ASC
                """,
                (conversation_id,),
            ).fetchall()

        if not rows:
            return {
                "conversation_id": conversation_id,
                "cost_usd": 0.0,
                "total_tokens": 0,
                "cycles": [],
            }

        total_cost = sum(float(r["cost_usd"] or 0) for r in rows)
        total_tokens = sum(
            int(r["prompt_tokens"] or 0) + int(r["completion_tokens"] or 0)
            for r in rows
        )

        cycles = [
            {
                "ts": r["ts"],
                "model": r["model"],
                "prompt_tokens": int(r["prompt_tokens"] or 0),
                "completion_tokens": int(r["completion_tokens"] or 0),
                "cost_usd": round(float(r["cost_usd"] or 0), 6),
                "tool_calls": int(r["tool_calls"] or 0),
                "latency_ms": r["latency_ms"],
                "outcome": r["outcome"],
            }
            for r in rows
        ]

        return {
            "conversation_id": conversation_id,
            "cost_usd": round(total_cost, 6),
            "total_tokens": total_tokens,
            "cycles": cycles,
        }


    def unsent_aggregates(self) -> list[UnsentAggregate]:
        """Return aggregated counters per (agent_id, day) for events not yet uploaded.

        Only numeric counters and identifiers are included — no content, no PII,
        no prompts, no URLs.  The returned event_ids are used internally to
        mark rows as uploaded after a successful cloud POST; they are NOT
        included in the body sent to the cloud.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_id,
                       substr(ts, 1, 10) AS day,
                       SUM(prompt_tokens)    AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens,
                       SUM(cost_usd)         AS cost_usd,
                       COUNT(*)              AS tasks,
                       SUM(CASE WHEN outcome NOT IN ('completed','pending_approval')
                                THEN 1 ELSE 0 END) AS failures,
                       GROUP_CONCAT(event_id) AS ids
                  FROM usage_events
                 WHERE uploaded = 0
                 GROUP BY agent_id, substr(ts, 1, 10)
                 ORDER BY day ASC
                """,
            ).fetchall()

        return [
            UnsentAggregate(
                agent_id=r["agent_id"],
                day=r["day"],
                prompt_tokens=int(r["prompt_tokens"] or 0),
                completion_tokens=int(r["completion_tokens"] or 0),
                cost_usd=float(r["cost_usd"] or 0.0),
                tasks=int(r["tasks"] or 0),
                failures=int(r["failures"] or 0),
                event_ids=tuple((r["ids"] or "").split(",")),
            )
            for r in rows
            if r["ids"]
        ]

    def mark_uploaded(self, event_ids: list[str]) -> None:
        """Flip uploaded=1 for the given event_ids (idempotent; batch update)."""
        if not event_ids:
            return
        placeholders = ",".join("?" * len(event_ids))
        with self._connect() as conn:
            conn.execute(
                f"UPDATE usage_events SET uploaded = 1 WHERE event_id IN ({placeholders})",
                event_ids,
            )


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _empty_summary(period: str) -> dict[str, Any]:
    return {
        "available": True,
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


def _count_self_hosted_cycles(conn: sqlite3.Connection, start_day: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
          FROM usage_events
         WHERE ts >= ?
           AND (cost_status = 'unknown' OR (cost_usd = 0 AND total_tokens > 0))
        """,
        (start_day + "T00:00:00",),
    ).fetchone()
    return int(row["cnt"] or 0) if row else 0


def _build_top_models(rows: list[sqlite3.Row], total_cost: float) -> list[dict[str, Any]]:
    models = [
        {
            "model": r["model"],
            "cost_usd": round(float(r["cost_usd"] or 0), 6),
            "share": _safe_share(float(r["cost_usd"] or 0), total_cost),
        }
        for r in rows
    ]
    models.sort(key=lambda m: m["cost_usd"], reverse=True)
    return models


def _safe_share(cost: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(cost / total, 4)
