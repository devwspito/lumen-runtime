"""Tests SQLiteUsageRepository — metering pipeline (Fase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.domain.cycle_output import TokenUsage
from hermes.shell_server.metering.usage_repo import SQLiteUsageRepository

pytestmark = pytest.mark.unit


def _make_usage(
    *,
    model: str = "qwen3",
    prompt: int = 100,
    completion: int = 50,
    cost: float = 0.0,
    cost_status: str = "unknown",
    provider: str = "vllm",
) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cost_usd=cost,
        model=model,
        cost_status=cost_status,
        cost_source="litellm",
        provider=provider,
    )


@pytest.fixture
def repo(tmp_path: Path) -> SQLiteUsageRepository:
    return SQLiteUsageRepository(db_path=tmp_path / "usage.db")


class TestRecordAndSummary:
    def test_summary_empty_when_no_data(self, repo: SQLiteUsageRepository) -> None:
        result = repo.summary(period="30d")
        assert result["available"] is True
        assert result["total_cost_usd"] == 0.0
        assert result["cycles"] == 0
        assert isinstance(result["top_models"], list)
        assert len(result["top_models"]) == 0

    def test_single_record_appears_in_summary(self, repo: SQLiteUsageRepository) -> None:
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id="conv-1",
            task_id="task-1",
            usage=_make_usage(prompt=200, completion=100, cost=0.005, cost_status="billed"),
            tool_calls=2,
            latency_ms=1234,
            outcome="completed",
        )
        result = repo.summary(period="30d")
        assert result["available"] is True
        assert result["cycles"] == 1
        assert result["total_tokens"] == 300
        assert result["total_cost_usd"] == pytest.approx(0.005, abs=1e-9)
        assert result["failures"] == 0
        assert len(result["top_models"]) == 1
        assert result["top_models"][0]["model"] == "qwen3"

    def test_two_records_same_day_agent_model_accumulate(
        self, repo: SQLiteUsageRepository
    ) -> None:
        usage = _make_usage(prompt=100, completion=50)
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id="conv-1",
            task_id="task-1",
            usage=usage,
            tool_calls=0,
            latency_ms=500,
            outcome="completed",
        )
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id="conv-1",
            task_id="task-2",
            usage=usage,
            tool_calls=1,
            latency_ms=600,
            outcome="completed",
        )
        result = repo.summary(period="30d")
        assert result["cycles"] == 2
        assert result["total_tokens"] == 300  # (100+50)*2

    def test_failed_outcome_counted_as_failure(self, repo: SQLiteUsageRepository) -> None:
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id=None,
            task_id="task-1",
            usage=_make_usage(),
            tool_calls=0,
            latency_ms=None,
            outcome="failed",
        )
        result = repo.summary(period="30d")
        assert result["failures"] == 1

    def test_pending_approval_not_counted_as_failure(
        self, repo: SQLiteUsageRepository
    ) -> None:
        repo.record_cycle(
            agent_id=None,
            conversation_id=None,
            task_id="task-2",
            usage=_make_usage(),
            tool_calls=1,
            latency_ms=200,
            outcome="pending_approval",
        )
        result = repo.summary(period="30d")
        assert result["failures"] == 0

    def test_self_hosted_cycles_counted_when_cost_unknown(
        self, repo: SQLiteUsageRepository
    ) -> None:
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id="conv-1",
            task_id="task-1",
            usage=_make_usage(cost=0.0, cost_status="unknown", prompt=50, completion=50),
            tool_calls=0,
            latency_ms=100,
            outcome="completed",
        )
        result = repo.summary(period="30d")
        assert result["self_hosted_cycles"] == 1


class TestByAgent:
    def test_empty_when_no_data(self, repo: SQLiteUsageRepository) -> None:
        result = repo.by_agent(period="30d")
        assert result["available"] is True
        assert isinstance(result["agents"], list)
        assert len(result["agents"]) == 0

    def test_groups_by_agent(self, repo: SQLiteUsageRepository) -> None:
        repo.record_cycle(
            agent_id="agent-A",
            conversation_id="c1",
            task_id="t1",
            usage=_make_usage(cost=0.01),
            tool_calls=0,
            latency_ms=None,
            outcome="completed",
        )
        repo.record_cycle(
            agent_id="agent-B",
            conversation_id="c2",
            task_id="t2",
            usage=_make_usage(cost=0.03),
            tool_calls=1,
            latency_ms=None,
            outcome="completed",
        )
        result = repo.by_agent(period="30d")
        agent_ids = {a["agent_id"] for a in result["agents"]}
        assert "agent-A" in agent_ids
        assert "agent-B" in agent_ids

    def test_share_sums_to_one(self, repo: SQLiteUsageRepository) -> None:
        repo.record_cycle(
            agent_id="agent-A",
            conversation_id=None,
            task_id="t1",
            usage=_make_usage(cost=0.01),
            tool_calls=0,
            latency_ms=None,
            outcome="completed",
        )
        repo.record_cycle(
            agent_id="agent-B",
            conversation_id=None,
            task_id="t2",
            usage=_make_usage(cost=0.03),
            tool_calls=0,
            latency_ms=None,
            outcome="completed",
        )
        result = repo.by_agent(period="30d")
        total_share = sum(a["share"] for a in result["agents"])
        assert total_share == pytest.approx(1.0, abs=0.01)


class TestTimeseries:
    def test_empty_when_no_data(self, repo: SQLiteUsageRepository) -> None:
        result = repo.timeseries(period="30d")
        assert result["available"] is True
        assert isinstance(result["points"], list)
        assert len(result["points"]) == 0

    def test_returns_points_after_record(self, repo: SQLiteUsageRepository) -> None:
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id=None,
            task_id="t1",
            usage=_make_usage(prompt=100, completion=50, cost=0.002),
            tool_calls=0,
            latency_ms=None,
            outcome="completed",
        )
        result = repo.timeseries(period="30d")
        assert len(result["points"]) >= 1
        point = result["points"][0]
        assert "day" in point
        assert "cost_usd" in point
        assert "tokens" in point
        assert "cycles" in point


class TestConversationUsage:
    def test_empty_conversation_returns_empty_cycles_list(
        self, repo: SQLiteUsageRepository
    ) -> None:
        result = repo.conversation_usage(conversation_id="nonexistent")
        assert result["conversation_id"] == "nonexistent"
        assert result["cost_usd"] == 0.0
        assert result["total_tokens"] == 0
        assert isinstance(result["cycles"], list)
        assert len(result["cycles"]) == 0

    def test_cycles_for_conversation(self, repo: SQLiteUsageRepository) -> None:
        usage = _make_usage(prompt=200, completion=100, cost=0.005)
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id="conv-X",
            task_id="t1",
            usage=usage,
            tool_calls=3,
            latency_ms=1000,
            outcome="completed",
        )
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id="conv-X",
            task_id="t2",
            usage=usage,
            tool_calls=1,
            latency_ms=500,
            outcome="completed",
        )
        # Record for a different conversation — must not bleed
        repo.record_cycle(
            agent_id="agent-1",
            conversation_id="conv-Y",
            task_id="t3",
            usage=usage,
            tool_calls=0,
            latency_ms=None,
            outcome="completed",
        )
        result = repo.conversation_usage(conversation_id="conv-X")
        assert result["conversation_id"] == "conv-X"
        assert len(result["cycles"]) == 2
        assert result["total_tokens"] == 300 * 2
        assert result["cost_usd"] == pytest.approx(0.01, abs=1e-9)
        # Each cycle dict has all required keys
        cycle = result["cycles"][0]
        for key in ("ts", "model", "prompt_tokens", "completion_tokens", "cost_usd",
                    "tool_calls", "latency_ms", "outcome"):
            assert key in cycle

    def test_arrays_never_none(self, repo: SQLiteUsageRepository) -> None:
        """All list fields must be lists, never None — prevents undefined.length crash."""
        summary = repo.summary(period="7d")
        assert summary["top_models"] is not None
        by_agent = repo.by_agent(period="7d")
        assert by_agent["agents"] is not None
        ts = repo.timeseries(period="7d")
        assert ts["points"] is not None
        cu = repo.conversation_usage(conversation_id="no-such")
        assert cu["cycles"] is not None
