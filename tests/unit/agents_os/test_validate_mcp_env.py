"""_validate_mcp_env (MCP-05, spec 025 matriz) — validated pattern + deny-list
replacing the fixed BYOK env-key allowlist.

Root cause (live-verified): the MCP form's OWN placeholder (McpView.tsx:1148,
`mcp.env.label`: "BRAVE_API_KEY=br-xxx") named a key that was never in the
old `_MCP_BYOK_ENV_KEYS` frozenset — anyone who followed the UI's own example
got a raw 400 `clave de env no permitida` back. Covers:
  - any plausible env-var name (`^[A-Z][A-Z0-9_]{2,63}$`) is now accepted,
    including the exact placeholder example;
  - the deny-list (PATH, LD_*, PYTHON*, HERMES_*, NODE_OPTIONS, SSL_CERT_*,
    http(s)_proxy variants) still rejects genuinely dangerous names;
  - HOME is the one deliberate exception — accepted here (R16: a MANAGED_
    REMOTE/OAuth-bridge draft's McpSpec.env legitimately carries it, and
    rejecting it here would hard-fail add_mcp_server before scan/prefetch/
    connect) even though the launcher never honours it as an override (see
    test_r16_mcp_bridge_handshake.py::TestLauncherHomeOwnership);
  - no error message ever echoes a VALUE, only key names;
  - OD_DAEMON_URL's URL validation is unchanged.
"""

from __future__ import annotations

import pytest

from hermes.agents_os.infrastructure.dbus_runtime_service import _validate_mcp_env

pytestmark = pytest.mark.unit


class TestPreviouslyRejectedButPlausibleKeysAreNowAccepted:
    def test_the_forms_own_placeholder_example_is_accepted(self) -> None:
        """The exact repro (spec 025 matriz MCP-05): BRAVE_API_KEY=br-xxx,
        verbatim from McpView.tsx's own `mcp.env.label` placeholder."""
        result = _validate_mcp_env({"BRAVE_API_KEY": "br-xxx"})
        assert result == {"BRAVE_API_KEY": "br-xxx"}

    @pytest.mark.parametrize(
        "key",
        ["BRAVE_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY", "GITHUB_TOKEN", "SLACK_BOT_TOKEN"],
    )
    def test_any_plausible_published_server_secret_name_is_accepted(self, key: str) -> None:
        result = _validate_mcp_env({key: "secret-value"})
        assert result == {key: "secret-value"}

    def test_the_old_curated_pack_still_validates_unchanged(self) -> None:
        """The fixed set this replaces is now just a SUBSET of what the
        pattern accepts — nothing that used to work should stop working."""
        result = _validate_mcp_env(
            {
                "REPLICATE_API_TOKEN": "r1",
                "CONTEXT7_API_KEY": "c1",
                "OPENAI_BASE_URL": "http://vllm.local",
                "OPENAI_API_KEY": "sk-1",
                "ADS_BEARER": "b1",
                "NODE_EXTRA_CA_CERTS": "/etc/ca.pem",
            }
        )
        assert len(result) == 6


class TestDenyListStillRejectsDangerousNames:
    @pytest.mark.parametrize(
        "key",
        [
            "PATH",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "HERMES_OPERATOR_ID",
            "HERMES_HOME",
            "NODE_OPTIONS",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
        ],
    )
    def test_dangerous_key_rejected(self, key: str) -> None:
        with pytest.raises(ValueError, match="clave de env no permitida"):
            _validate_mcp_env({key: "value"})

    def test_error_message_never_echoes_the_value(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            _validate_mcp_env({"LD_PRELOAD": "super-secret-payload-path"})
        assert "super-secret-payload-path" not in str(exc_info.value)

    def test_lowercase_dangerous_key_also_rejected_by_the_pattern(self) -> None:
        """The allow PATTERN itself only ever matches upper-case names — a
        lowercase dangerous key never even reaches the deny-list check."""
        with pytest.raises(ValueError, match="clave de env no permitida"):
            _validate_mcp_env({"path": "value"})


class TestHomeIsTheOneDeliberateException:
    """R16 (test_r16_mcp_bridge_handshake.py) — preserved verbatim."""

    def test_home_is_accepted_at_this_validation_layer(self) -> None:
        result = _validate_mcp_env({"HOME": "/var/lib/hermes"})
        assert result == {"HOME": "/var/lib/hermes"}


class TestValidationStillWorksAsBefore:
    def test_non_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="diccionario"):
            _validate_mcp_env(["not", "a", "dict"])

    def test_non_string_key_raises(self) -> None:
        with pytest.raises(ValueError, match="no es string"):
            _validate_mcp_env({1: "value"})

    def test_empty_value_raises(self) -> None:
        with pytest.raises(ValueError, match="no vacío"):
            _validate_mcp_env({"BRAVE_API_KEY": ""})

    def test_non_string_value_raises(self) -> None:
        with pytest.raises(ValueError, match="no vacío"):
            _validate_mcp_env({"BRAVE_API_KEY": 12345})

    def test_od_daemon_url_still_validated_as_url(self) -> None:
        with pytest.raises(ValueError, match="URL"):
            _validate_mcp_env({"OD_DAEMON_URL": "not-a-url"})

    def test_od_daemon_url_accepts_a_real_url(self) -> None:
        result = _validate_mcp_env({"OD_DAEMON_URL": "https://od.example.com"})
        assert result == {"OD_DAEMON_URL": "https://od.example.com"}

    def test_key_too_short_rejected(self) -> None:
        """Pattern requires >= 3 chars total (`^[A-Z][A-Z0-9_]{2,63}$`)."""
        with pytest.raises(ValueError, match="clave de env no permitida"):
            _validate_mcp_env({"AB": "value"})

    def test_key_too_long_rejected(self) -> None:
        with pytest.raises(ValueError, match="clave de env no permitida"):
            _validate_mcp_env({"A" * 65: "value"})
