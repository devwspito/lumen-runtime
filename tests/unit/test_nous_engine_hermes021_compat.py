"""Regression tests for the hermes-agent 0.21.1 (v2026.9.7) engine upgrade (specs/023).

Runs WITHOUT hermes-agent installed: the upstream modules are stubbed in sys.modules.

  (a) GovernedAIAgent._invoke_tool accepts and forwards the kwargs the 0.21 tool
      executor passes (skip_tool_request_middleware, skip_tool_execution_middleware,
      tool_request_middleware_trace). Before the fix every concurrent tool call raised
      TypeError inside the executor and surfaced as "Error executing tool".
  (b) _build_governed_agent always passes an explicit max_iterations ceiling — 0.21
      made the AIAgent default unlimited (sys.maxsize); 0.15 capped it at 90.
  (c) The gated memory / clarify wrappers carry the 0.21 arguments (new_text,
      operations / questions, multi_select) into the broker proposal.
"""
from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from hermes.prompts.persona import PersonaSpec
from hermes.runtime.model_config import ModelConfig
from hermes.runtime.nous_engine import (
    _NOUS_LEGACY_MAX_ITERATIONS,
    GovernedAIAgent,
    NousReasoningEngine,
    _patch_clarify_tool,
    _patch_memory_tool,
)
from hermes.runtime.nous_tool_risk_map import NousRisk, classify_nous_tool

pytestmark = pytest.mark.unit

_TENANT = UUID("00000000-0000-0000-0000-000000000001")
_OPERATOR = UUID("00000000-0000-0000-0000-000000000002")

_RUNTIME = (
    {
        "api_key": None,
        "base_url": None,
        "provider": "openai-api",
        "api_mode": "chat_completions",
        "credential_pool": None,
    },
    "test-model",
)


def _consent_ctx() -> Any:
    from hermes.capabilities.domain.ports import ConsentContext  # noqa: PLC0415

    return ConsentContext(
        tenant_id=_TENANT, operator_id=_OPERATOR, derived_from_untrusted_content=False
    )


def _persona() -> PersonaSpec:
    return PersonaSpec(
        name="Safent", role="asistente", language="es-ES", register="", primary_mission="ayudar"
    )


def _governed_agent() -> GovernedAIAgent:
    with patch("hermes.runtime.nous_engine._import_ai_agent") as mock_import:
        mock_import.return_value = MagicMock(return_value=MagicMock())
        return GovernedAIAgent(
            model="test/model", consent_context=_consent_ctx(), tenant_id=_TENANT
        )


def _stub_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: Any) -> types.ModuleType:
    """Register a stub upstream module (and its parent package) in sys.modules."""
    parent = name.rsplit(".", 1)[0]
    if parent not in sys.modules:
        monkeypatch.setitem(sys.modules, parent, types.ModuleType(parent))
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


class TestInvokeToolForwardsExecutorKwargs:
    def test_read_tool_forwards_middleware_kwargs_to_native_invoke(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert classify_nous_tool("web_search") is NousRisk.READ
        invoke_tool = MagicMock(return_value='{"results": []}')
        _stub_module(monkeypatch, "agent.agent_runtime_helpers", invoke_tool=invoke_tool)
        agent = _governed_agent()
        trace = [{"middleware": "m1"}]

        result = agent._invoke_tool(
            "web_search", {"query": "x"}, "task-1", "call-1",
            messages=[], pre_tool_block_checked=True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
            tool_request_middleware_trace=trace,
        )

        assert result == '{"results": []}'
        invoke_tool.assert_called_once_with(
            agent._inner, "web_search", {"query": "x"}, "task-1", "call-1", [], True,
            skip_tool_request_middleware=True,
            skip_tool_execution_middleware=True,
            tool_request_middleware_trace=trace,
        )

    def test_legacy_call_shape_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        invoke_tool = MagicMock(return_value="ok")
        _stub_module(monkeypatch, "agent.agent_runtime_helpers", invoke_tool=invoke_tool)
        agent = _governed_agent()

        assert agent._invoke_tool("web_search", {"query": "x"}, "task-1") == "ok"
        invoke_tool.assert_called_once_with(
            agent._inner, "web_search", {"query": "x"}, "task-1", None, None, False
        )


class TestMaxIterationsCeiling:
    def _build(self, model_config: ModelConfig) -> dict:
        captured: dict = {}

        def fake_ai_agent_cls(*_args: Any, **kwargs: Any) -> MagicMock:
            captured.update(kwargs)
            return MagicMock()

        engine = NousReasoningEngine(persona=_persona())
        loop = asyncio.new_event_loop()
        try:
            with (
                patch(
                    "hermes.runtime.nous_engine._import_ai_agent",
                    return_value=fake_ai_agent_cls,
                ),
                patch(
                    "hermes.runtime.nous_engine._cached_resolve_hermes_runtime",
                    return_value=_RUNTIME,
                ),
                patch(
                    "hermes.runtime.nous_engine._cached_enrich_prompt",
                    side_effect=lambda prompt, _tenant: prompt,
                ),
            ):
                engine._build_governed_agent(model_config, "system prompt", loop, _TENANT, None)
        finally:
            loop.close()
        return captured

    def test_default_config_pins_pre_021_ceiling(self) -> None:
        assert _NOUS_LEGACY_MAX_ITERATIONS == 90
        captured = self._build(ModelConfig(model="test/model"))
        assert captured["max_iterations"] == _NOUS_LEGACY_MAX_ITERATIONS

    def test_operator_value_wins(self) -> None:
        captured = self._build(ModelConfig(model="test/model", max_iterations=25))
        assert captured["max_iterations"] == 25


class TestGatedWrappersCarry021Arguments:
    def test_memory_replace_keeps_new_text_and_drops_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mem_mod = _stub_module(monkeypatch, "tools.memory_tool", memory_tool=MagicMock())
        agent = _governed_agent()
        with patch.object(
            agent, "_dispatch_write_proposal", return_value='{"ok": true}'
        ) as dispatch:
            _patch_memory_tool(agent)
            mem_mod.memory_tool(
                action="replace", target="memory", old_text="a", new_text="b", operations=None
            )
        args = dispatch.call_args.kwargs["function_args"]
        assert args["old_text"] == "a"
        assert args["new_text"] == "b"
        assert "operations" not in args

    def test_clarify_carries_questions_and_multi_select(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clarify_mod = _stub_module(monkeypatch, "tools.clarify_tool", clarify_tool=MagicMock())
        agent = _governed_agent()
        questions = [{"question": "q1", "choices": ["a", "b"]}]
        with patch.object(
            agent, "_dispatch_write_proposal", return_value='{"ok": true}'
        ) as dispatch:
            _patch_clarify_tool(agent)
            clarify_mod.clarify_tool(question="", questions=questions, multi_select=True)
        args = dispatch.call_args.kwargs["function_args"]
        assert args["questions"] == questions
        assert args["multi_select"] is True
