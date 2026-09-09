"""Regresión: cada seed MCP baked tiene que arrancar OFFLINE en un primer boot.

Bug (imagen `feat/safent-next`): el seed `powerpoint` moría en el import antes
de emitir una sola línea de JSON-RPC —

    ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
    where FastMCP was renamed to MCPServer …

`office-powerpoint-mcp-server` declara `mcp` sin techo, así que uv resolvía el
SDK 2.0 recién publicado y el paquete (escrito contra la API v1) reventaba. El
seed lleva ahora `--with mcp<2`.

Invariante que se fija aquí: el spec con el que el Containerfile CALIENTA la
caché de uv debe ser idéntico, token a token, al argv del seed. uv cachea el
entorno por sus requisitos y el arranque real añade `--offline`: cualquier
diferencia entre los dos (una restricción en uno y no en el otro) es un fallo
de caché en el primer boot, sin red para arreglarlo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEED_FILE = _REPO_ROOT / "ops" / "agents-os-edition" / "seed" / "mcp-servers.json"
_CONTAINERFILE = _REPO_ROOT / "ops" / "container" / "Containerfile"


def _seeds() -> list[dict]:
    return json.loads(_SEED_FILE.read_text(encoding="utf-8"))


def _warmed_specs() -> list[list[str]]:
    """Los `spec` del bucle `for spec in … ; do … uvx $spec` del Containerfile."""
    source = _CONTAINERFILE.read_text(encoding="utf-8")
    start = source.index('for spec in "excel-mcp-server stdio"')
    end = source.index("; do", start)
    return [spec.split() for spec in re.findall(r'"([^"]+)"', source[start:end])]


class TestPowerpointSeedPinsMcpV1:
    def test_powerpoint_argv_constrains_mcp_below_2(self) -> None:
        argv = next(s["argv"] for s in _seeds() if s["server_id"] == "powerpoint")
        assert "--with" in argv
        assert argv[argv.index("--with") + 1] == "mcp<2"

    def test_constraint_comes_after_the_from_package(self) -> None:
        """El gate del scanner (`_scanner_can_analyze_argv`) resuelve el PRIMER
        `--from`/`--with` que encuentra: si `--with mcp<2` se colara delante,
        el argv se analizaría contra `mcp<2` en vez de contra el paquete."""
        argv = next(s["argv"] for s in _seeds() if s["server_id"] == "powerpoint")
        assert argv.index("--from") < argv.index("--with")

    def test_scanner_still_accepts_the_constrained_argv(self) -> None:
        from hermes.agents_os.infrastructure.dbus_runtime_service import (
            _scanner_can_analyze_argv,
        )

        argv = next(s["argv"] for s in _seeds() if s["server_id"] == "powerpoint")
        assert _scanner_can_analyze_argv(argv)


class TestWarmedSpecsMatchSeedArgv:
    def test_every_seed_argv_is_warmed_verbatim(self) -> None:
        warmed = _warmed_specs()
        for seed in _seeds():
            argv = seed["argv"]
            assert argv[0] == "uvx", f"{seed['server_id']}: seed no-uvx sin calentar"
            assert argv[1:] in warmed, (
                f"{seed['server_id']}: el Containerfile calienta {warmed!r}, "
                f"que no incluye {argv[1:]!r} — `uvx --offline` no encontrará "
                "ese entorno en la caché en el primer boot"
            )
