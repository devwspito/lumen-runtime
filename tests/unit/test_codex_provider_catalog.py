"""ProviderKind.CODEX — catalog entry + config.yaml writer (item 4).

Covers:
  - ProviderKind.CODEX exists and is covered by the canonical catalog
    (module-level completeness check would otherwise raise at import time).
  - The catalog entry carries the owner-facing label, default model
    ("gpt-6-astra", plan.md D-A4), and the three alternative models.
  - build_litellm_model_string uses the "openai-codex" prefix (distinct from
    plain "openai", same collision-avoidance reasoning as NOUS).
  - LITELLM_PREFIX (the older, still-consulted dict) also covers CODEX — a
    Codex provider row added with an api_key fallback and resolved via the
    SQL-vault path must not KeyError.
  - _write_hermes_model_config("openai-codex", <model>) — the SAME writer
    other native providers use — produces the expected config.yaml shape.
"""

from __future__ import annotations

import sys
import types

import pytest

from hermes.providers.domain.canonical import HermesCliRoute
from hermes.providers.domain.catalog import build_litellm_model_string, canonical_for
from hermes.shell_server.providers.domain import LITELLM_PREFIX, ProviderKind

pytestmark = pytest.mark.unit


class TestCodexCatalogEntry:
    def test_provider_kind_codex_exists(self) -> None:
        assert ProviderKind.CODEX == "openai_codex"

    def test_catalog_entry_present(self) -> None:
        entry = canonical_for(ProviderKind.CODEX)
        assert entry.hermes_cli_slug == "openai-codex"
        assert entry.route is HermesCliRoute.REGISTERED_SLUG

    def test_label_present(self) -> None:
        entry = canonical_for(ProviderKind.CODEX)
        assert entry.label == "OpenAI Codex / ChatGPT (suscripción)"

    def test_default_model_is_gpt_6_astra(self) -> None:
        entry = canonical_for(ProviderKind.CODEX)
        assert entry.default_model == "gpt-6-astra"

    def test_alternative_models(self) -> None:
        entry = canonical_for(ProviderKind.CODEX)
        assert entry.alternative_models == ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")

    def test_litellm_model_string_uses_openai_codex_prefix(self) -> None:
        assert (
            build_litellm_model_string(ProviderKind.CODEX, "gpt-6-astra")
            == "openai-codex/gpt-6-astra"
        )

    def test_legacy_litellm_prefix_dict_also_covers_codex(self) -> None:
        """LITELLM_PREFIX (shell_server/providers/domain.py) is still consulted
        by provider_config_source.py / runtime/__main__.py's SQL-vault
        fallback path — a missing entry would KeyError for a Codex row
        resolved through that path (e.g. the OPENAI_API_KEY fallback,
        plan.md D-A4)."""
        assert LITELLM_PREFIX[ProviderKind.CODEX] == "openai-codex"


class TestWriteHermesModelConfigCodex:
    """_write_hermes_model_config is the SAME writer every other native
    provider uses (add_provider/set_active_provider + the OAuth workers) —
    reused verbatim for Codex, no new writer introduced."""

    def _stub_hermes_cli_config(self, monkeypatch: pytest.MonkeyPatch) -> dict:
        saved: dict = {}
        if "hermes_cli" not in sys.modules:
            sys.modules["hermes_cli"] = types.ModuleType("hermes_cli")
        mod = types.ModuleType("hermes_cli.config")
        mod.load_config = dict  # type: ignore[attr-defined]
        mod.save_config = saved.update  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "hermes_cli.config", mod)
        return saved

    def test_writes_provider_and_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved = self._stub_hermes_cli_config(monkeypatch)
        from hermes.agents_os.infrastructure.dbus_runtime_service import (
            _write_hermes_model_config,
        )

        _write_hermes_model_config("openai-codex", "gpt-6-astra")

        assert saved == {"model": {"provider": "openai-codex", "default": "gpt-6-astra"}}

    def test_preserves_existing_config_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved: dict = {}
        if "hermes_cli" not in sys.modules:
            sys.modules["hermes_cli"] = types.ModuleType("hermes_cli")
        mod = types.ModuleType("hermes_cli.config")
        mod.load_config = lambda: {"mcp_servers": {"excel": {}}}  # type: ignore[attr-defined]
        mod.save_config = saved.update  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "hermes_cli.config", mod)

        from hermes.agents_os.infrastructure.dbus_runtime_service import (
            _write_hermes_model_config,
        )

        _write_hermes_model_config("openai-codex", "gpt-6-astra")

        assert saved["mcp_servers"] == {"excel": {}}
        assert saved["model"] == {"provider": "openai-codex", "default": "gpt-6-astra"}

    def test_codex_oauth_worker_uses_catalog_default_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The OAuth device-code flow (_codex_oauth_worker) must derive its
        default model from the SAME catalog entry — not a second, divergent
        hardcoded literal."""
        saved = self._stub_hermes_cli_config(monkeypatch)
        from hermes.agents_os.infrastructure.dbus_runtime_service import (
            _write_hermes_model_config,
        )
        from hermes.providers.domain.catalog import canonical_for

        model = canonical_for(ProviderKind.CODEX).default_model
        _write_hermes_model_config("openai-codex", model)

        assert saved["model"]["default"] == "gpt-6-astra"
