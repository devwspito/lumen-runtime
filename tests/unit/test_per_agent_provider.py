"""Tests for per-agent provider binding (Fase 3c).

Covers:
1. Domain: provider_alias field on Agent and AgentDraft; default_agent has None.
2. Registry: provider_alias persists in create/update/get; migration idempotent
   on a DB without the column.
3. Serialization: agent_to_dict includes provider_alias; draft_from_dict parses it;
   empty string normalised to None.
4. Resolver: resolve_by_alias returns the correct ResolvedModel; None when not found.
5. Engine: _resolve_model_config uses the per-agent provider when the agent has
   provider_alias + model_config_for_alias wired; falls back to global when not;
   falls back when agent_id is None.
6. Retro-compat: engine built WITHOUT model_config_for_alias behaves identically
   to before (no regression).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from hermes.agents.domain.agent import (
    Agent,
    AgentDraft,
    AutonomyLevel,
    default_agent,
)
from hermes.agents.application.serialization import agent_to_dict, draft_from_dict

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. Domain
# ---------------------------------------------------------------------------


class TestProviderAliasDomain:
    def test_default_agent_provider_alias_is_none(self) -> None:
        assert default_agent().provider_alias is None

    def test_agent_draft_provider_alias_defaults_to_none(self) -> None:
        draft = AgentDraft(name="X")
        assert draft.provider_alias is None

    def test_agent_draft_accepts_provider_alias(self) -> None:
        draft = AgentDraft(name="X", provider_alias="my-vllm")
        assert draft.provider_alias == "my-vllm"

    def test_agent_dataclass_accepts_provider_alias(self) -> None:
        from datetime import datetime, UTC
        agent = Agent(
            agent_id="a1",
            name="Test",
            provider_alias="openai-prod",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        assert agent.provider_alias == "openai-prod"


# ---------------------------------------------------------------------------
# 2. Registry: persistence and idempotent migration
# ---------------------------------------------------------------------------


class TestProviderAliasRegistry:
    def test_create_agent_with_provider_alias_persists(self, tmp_path) -> None:
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

        reg = SqliteAgentRegistry(db_path=tmp_path / "s.db")
        created = reg.create_agent(AgentDraft(name="SpecialistA", provider_alias="my-vllm"))
        fetched = reg.get_agent(created.agent_id)
        assert fetched.provider_alias == "my-vllm"

    def test_create_agent_without_alias_has_none(self, tmp_path) -> None:
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

        reg = SqliteAgentRegistry(db_path=tmp_path / "s.db")
        created = reg.create_agent(AgentDraft(name="NoAlias"))
        fetched = reg.get_agent(created.agent_id)
        assert fetched.provider_alias is None

    def test_update_agent_sets_provider_alias(self, tmp_path) -> None:
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

        reg = SqliteAgentRegistry(db_path=tmp_path / "s.db")
        created = reg.create_agent(AgentDraft(name="Bot"))
        reg.update_agent(
            created.agent_id,
            AgentDraft(name="Bot", provider_alias="anthropic-prod"),
        )
        fetched = reg.get_agent(created.agent_id)
        assert fetched.provider_alias == "anthropic-prod"

    def test_update_agent_clears_provider_alias(self, tmp_path) -> None:
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

        reg = SqliteAgentRegistry(db_path=tmp_path / "s.db")
        created = reg.create_agent(AgentDraft(name="Bot", provider_alias="my-vllm"))
        reg.update_agent(
            created.agent_id,
            AgentDraft(name="Bot", provider_alias=None),
        )
        fetched = reg.get_agent(created.agent_id)
        assert fetched.provider_alias is None

    def test_migration_idempotent_on_db_without_column(self, tmp_path) -> None:
        """Second construction on same DB (no provider_alias column) must not raise."""
        import sqlite3
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

        db = tmp_path / "old.db"
        # Build once — applies all migrations including provider_alias.
        SqliteAgentRegistry(db_path=db)
        # Build again — idempotent ALTER TABLE must not raise.
        reg2 = SqliteAgentRegistry(db_path=db)
        # Verify reading still works.
        agents = reg2.list_agents()
        assert any(a.provider_alias is None for a in agents)

    def test_migration_on_db_manually_missing_column(self, tmp_path) -> None:
        """Simulate a legacy DB without provider_alias; the registry must apply the migration."""
        import sqlite3
        from hermes.agents.infrastructure.sqlite_agent_registry import (
            _SCHEMA,
            SqliteAgentRegistry,
        )

        db = tmp_path / "legacy.db"
        # Create a DB with the base schema but WITHOUT provider_alias.
        conn = sqlite3.connect(str(db))
        conn.executescript("PRAGMA journal_mode=WAL;")
        conn.executescript(_SCHEMA)
        # Add autonomy_level and department but NOT provider_alias (simulates old DB).
        try:
            conn.execute(
                "ALTER TABLE agents ADD COLUMN autonomy_level TEXT NOT NULL DEFAULT 'balanced'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE agents ADD COLUMN department TEXT")
        except sqlite3.OperationalError:
            pass
        conn.close()

        # Registry construction should apply the provider_alias migration without error.
        reg = SqliteAgentRegistry(db_path=db)
        # Must be able to create and fetch agents without error.
        created = reg.create_agent(AgentDraft(name="Legacy", provider_alias="x"))
        fetched = reg.get_agent(created.agent_id)
        assert fetched.provider_alias == "x"

    def test_default_agent_provider_alias_is_none_in_registry(self, tmp_path) -> None:
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry
        from hermes.agents.domain.agent import DEFAULT_AGENT_ID

        reg = SqliteAgentRegistry(db_path=tmp_path / "s.db")
        default = reg.get_agent(DEFAULT_AGENT_ID)
        assert default.provider_alias is None


# ---------------------------------------------------------------------------
# 3. Serialization
# ---------------------------------------------------------------------------


class TestProviderAliasSerialization:
    def test_agent_to_dict_includes_provider_alias(self, tmp_path) -> None:
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

        reg = SqliteAgentRegistry(db_path=tmp_path / "s.db")
        agent = reg.create_agent(AgentDraft(name="A", provider_alias="gpt-corp"))
        d = agent_to_dict(agent)
        assert d["provider_alias"] == "gpt-corp"

    def test_agent_to_dict_null_alias_is_none(self, tmp_path) -> None:
        from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

        reg = SqliteAgentRegistry(db_path=tmp_path / "s.db")
        agent = reg.create_agent(AgentDraft(name="A"))
        d = agent_to_dict(agent)
        assert d["provider_alias"] is None

    def test_draft_from_dict_parses_provider_alias(self) -> None:
        draft = draft_from_dict({"name": "X", "provider_alias": "my-vllm"})
        assert draft.provider_alias == "my-vllm"

    def test_draft_from_dict_missing_alias_is_none(self) -> None:
        draft = draft_from_dict({"name": "X"})
        assert draft.provider_alias is None

    def test_draft_from_dict_empty_string_alias_is_none(self) -> None:
        # Empty string normalised to None at the trust boundary.
        draft = draft_from_dict({"name": "X", "provider_alias": "   "})
        assert draft.provider_alias is None

    def test_draft_from_dict_explicit_none_alias(self) -> None:
        draft = draft_from_dict({"name": "X", "provider_alias": None})
        assert draft.provider_alias is None


# ---------------------------------------------------------------------------
# 4. Resolver: resolve_by_alias
# ---------------------------------------------------------------------------


class TestResolveByAlias:
    def _make_fake_provider(self, alias: str = "my-vllm"):
        from hermes.shell_server.providers.domain import Provider, ProviderKind
        from uuid import uuid4
        from datetime import datetime, UTC
        return Provider(
            provider_id=uuid4(),
            alias=alias,
            kind=ProviderKind.VLLM,
            base_url="http://localhost:8000/v1",
            has_api_key=False,
            default_model="qwen3-35b",
            enabled=True,
            is_active=False,
            created_at=datetime.now(tz=UTC),
        )

    def test_resolve_by_alias_returns_resolved_model(self) -> None:
        from hermes.providers.infrastructure.vault_provider_resolver import VaultProviderResolver

        provider = self._make_fake_provider("my-vllm")
        mock_repo = MagicMock()
        mock_repo.get_by_alias.return_value = provider
        mock_repo.reveal_api_key.return_value = None

        resolver = VaultProviderResolver(repo=mock_repo)
        result = resolver.resolve_by_alias("my-vllm")

        assert result is not None
        assert result.provider.alias == "my-vllm"
        mock_repo.get_by_alias.assert_called_once_with("my-vllm")

    def test_resolve_by_alias_returns_none_when_not_found(self) -> None:
        from hermes.providers.infrastructure.vault_provider_resolver import VaultProviderResolver

        mock_repo = MagicMock()
        mock_repo.get_by_alias.return_value = None

        resolver = VaultProviderResolver(repo=mock_repo)
        result = resolver.resolve_by_alias("nonexistent")

        assert result is None

    def test_resolve_by_alias_fail_soft_on_repo_error(self) -> None:
        from hermes.providers.infrastructure.vault_provider_resolver import VaultProviderResolver

        mock_repo = MagicMock()
        mock_repo.get_by_alias.side_effect = RuntimeError("DB down")

        resolver = VaultProviderResolver(repo=mock_repo)
        result = resolver.resolve_by_alias("boom")

        assert result is None

    def test_repo_get_by_alias_returns_correct_provider(self, tmp_path) -> None:
        """get_by_alias on the real SQLite repo finds by alias."""
        from hermes.shell_server.providers.repo import SQLiteProviderRepository
        from hermes.shell_server.providers.domain import new_provider, ProviderKind
        from unittest.mock import MagicMock

        # Stub out SecretsVault — not needed for a no-key provider.
        vault = MagicMock()
        vault.encrypt.return_value = b""
        vault.decrypt.return_value = ""

        repo = SQLiteProviderRepository(db_path=tmp_path / "p.db", vault=vault)
        p = new_provider(alias="test-vllm", kind=ProviderKind.VLLM, default_model="q3")
        repo.add(provider=p, api_key=None)

        found = repo.get_by_alias("test-vllm")
        assert found is not None
        assert found.alias == "test-vllm"

    def test_repo_get_by_alias_returns_none_for_unknown(self, tmp_path) -> None:
        from hermes.shell_server.providers.repo import SQLiteProviderRepository
        from unittest.mock import MagicMock

        vault = MagicMock()
        repo = SQLiteProviderRepository(db_path=tmp_path / "p.db", vault=vault)
        assert repo.get_by_alias("unknown-alias") is None


# ---------------------------------------------------------------------------
# 5. Engine: _resolve_model_config with per-agent binding
# ---------------------------------------------------------------------------


def _make_fake_agent_registry(agent_id: str, provider_alias: str | None):
    """Fake agent registry that returns an agent with the given provider_alias."""
    from datetime import datetime, UTC

    agent = Agent(
        agent_id=agent_id,
        name="TestAgent",
        provider_alias=provider_alias,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    registry = MagicMock()
    registry.get_agent.return_value = agent
    return registry


def _make_global_model_config(model: str = "openai/global-model"):
    from hermes.runtime.model_config import ModelConfig
    return ModelConfig(model=model, api_key="global-key")


def _make_per_agent_model_config(model: str = "hosted_vllm/agent-model"):
    from hermes.runtime.model_config import ModelConfig
    return ModelConfig(model=model, api_key=None, base_url="http://localhost:8000/v1")


class TestEnginePerAgentProvider:
    def _make_engine(
        self,
        *,
        agent_registry=None,
        model_config_for_alias=None,
        model_config_source=None,
    ):
        """Build a NousReasoningEngine with minimal wiring (no Nous binary needed)."""
        from hermes.runtime.nous_engine import NousReasoningEngine
        from hermes.prompts.persona import PersonaSpec
        from unittest.mock import patch

        persona = PersonaSpec(
            name="Test",
            role="test",
            language="es",
            register="test",
            primary_mission="test",
        )
        with patch(
            "hermes.runtime.nous_engine.install_thread_local_cdp_override"
        ), patch(
            "hermes.runtime.nous_engine.install_jail_block_local_session",
            create=True,
        ):
            engine = NousReasoningEngine(
                persona=persona,
                agent_registry=agent_registry,
                model_config_for_alias=model_config_for_alias,
                model_config_source=model_config_source,
            )
        return engine

    def test_resolves_per_agent_provider_when_alias_set(self) -> None:
        agent_id = "agent-abc"
        alias = "my-vllm"
        per_agent_cfg = _make_per_agent_model_config()
        global_cfg = _make_global_model_config()

        registry = _make_fake_agent_registry(agent_id, alias)

        def model_config_for_alias(a: str):
            return per_agent_cfg if a == alias else None

        engine = self._make_engine(
            agent_registry=registry,
            model_config_for_alias=model_config_for_alias,
            model_config_source=lambda: global_cfg,
        )

        result = engine._resolve_model_config(agent_id)
        assert result is per_agent_cfg

    def test_falls_back_to_global_when_agent_has_no_alias(self) -> None:
        agent_id = "agent-no-alias"
        global_cfg = _make_global_model_config()

        registry = _make_fake_agent_registry(agent_id, provider_alias=None)
        engine = self._make_engine(
            agent_registry=registry,
            model_config_for_alias=lambda a: _make_per_agent_model_config(),
            model_config_source=lambda: global_cfg,
        )

        result = engine._resolve_model_config(agent_id)
        assert result is global_cfg

    def test_falls_back_to_global_when_alias_resolver_returns_none(self) -> None:
        agent_id = "agent-bad-alias"
        global_cfg = _make_global_model_config()

        registry = _make_fake_agent_registry(agent_id, provider_alias="nonexistent")
        engine = self._make_engine(
            agent_registry=registry,
            model_config_for_alias=lambda a: None,  # alias not found
            model_config_source=lambda: global_cfg,
        )

        result = engine._resolve_model_config(agent_id)
        assert result is global_cfg

    def test_falls_back_to_global_when_no_agent_id(self) -> None:
        global_cfg = _make_global_model_config()
        engine = self._make_engine(
            agent_registry=_make_fake_agent_registry("x", "some-alias"),
            model_config_for_alias=lambda a: _make_per_agent_model_config(),
            model_config_source=lambda: global_cfg,
        )

        result = engine._resolve_model_config(None)
        assert result is global_cfg

    def test_falls_back_to_global_when_agent_registry_absent(self) -> None:
        global_cfg = _make_global_model_config()
        engine = self._make_engine(
            agent_registry=None,
            model_config_for_alias=lambda a: _make_per_agent_model_config(),
            model_config_source=lambda: global_cfg,
        )

        result = engine._resolve_model_config("some-agent")
        assert result is global_cfg

    def test_falls_back_to_global_when_model_config_for_alias_absent(self) -> None:
        agent_id = "agent-xyz"
        global_cfg = _make_global_model_config()
        registry = _make_fake_agent_registry(agent_id, provider_alias="some-alias")

        engine = self._make_engine(
            agent_registry=registry,
            model_config_for_alias=None,
            model_config_source=lambda: global_cfg,
        )

        result = engine._resolve_model_config(agent_id)
        assert result is global_cfg


# ---------------------------------------------------------------------------
# 6. Retro-compatibility: engine built without model_config_for_alias
# ---------------------------------------------------------------------------


class TestEngineRetroCompat:
    def _make_legacy_engine(self, *, model_config_source=None):
        """Build engine WITHOUT model_config_for_alias — simulates pre-3c deployment."""
        from hermes.runtime.nous_engine import NousReasoningEngine
        from hermes.prompts.persona import PersonaSpec
        from unittest.mock import patch

        persona = PersonaSpec(
            name="Legacy",
            role="test",
            language="es",
            register="test",
            primary_mission="test",
        )
        with patch(
            "hermes.runtime.nous_engine.install_thread_local_cdp_override"
        ), patch(
            "hermes.runtime.nous_engine.install_jail_block_local_session",
            create=True,
        ):
            engine = NousReasoningEngine(
                persona=persona,
                model_config_source=model_config_source,
                # model_config_for_alias intentionally omitted
            )
        return engine

    def test_legacy_engine_uses_global_source(self) -> None:
        global_cfg = _make_global_model_config()
        engine = self._make_legacy_engine(model_config_source=lambda: global_cfg)
        result = engine._resolve_model_config("any-agent-id")
        assert result is global_cfg

    def test_legacy_engine_falls_back_to_env_when_no_source(self) -> None:
        from hermes.runtime.model_config import ModelConfig
        engine = self._make_legacy_engine(model_config_source=None)
        with patch.object(ModelConfig, "from_env") as mock_env:
            mock_env.return_value = _make_global_model_config("env/model")
            result = engine._resolve_model_config(None)
        assert result.model == "env/model"
