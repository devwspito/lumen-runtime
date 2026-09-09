"""Regresión: el puente de fds del launcher debe completar el handshake MCP
contra el SDK 1.x Y el SDK 2.0.

Bug (imagen `feat/safent-next`, Hermes 0.21.1 + MCP SDK 2.0.0): TODOS los
servidores MCP stdio sembrados fallaban el handshake ~120 s después de
arrancar, con un mensaje de excepción VACÍO:

    hermes.dbus.mcp_reconnect_failed server=excel: ... StdioMcpClient:
    handshake failed via launcher for ['uvx','excel-mcp-server','stdio']:

Causa raíz: en el SDK 2.0 `mcp.types.JSONRPCMessage` dejó de ser un `RootModel`
de pydantic y pasó a ser un alias de unión PEP-604 (`types.UnionType`). El pump
de lectura de `_wire_launcher_streams` seguía llamando
`JSONRPCMessage.model_validate_json(line)`, que revienta con

    AttributeError: 'types.UnionType' object has no attribute
    'model_validate_json'

en CADA línea entrante. El `except` mete la excepción en el read stream (idioma
del propio SDK para líneas corruptas), pero el dispatcher del SDK 2.0
(`JsonRpcDispatcher._dispatch`) descarta los `Exception` a nivel DEBUG en vez de
romper la sesión → `initialize()` nunca ve su respuesta (que SÍ había llegado) y
expira. `asyncio.timeout` levanta `TimeoutError()`, cuyo `str()` es "" — de ahí
el mensaje vacío.

Cobertura:
  (a) `_jsonrpc_line_decoder` decodifica con las DOS formas de JSONRPCMessage.
  (b) handshake real (initialize + tools/list) sobre pipes de verdad, a través
      de `_wire_launcher_streams` + `_start_session_owner` — el mismo camino de
      código que usa producción con los fds que llegan por SCM_RIGHTS.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake MCP stdio server — habla JSON-RPC por line-delimited JSON sobre pipes
# ---------------------------------------------------------------------------


_FAKE_TOOLS = [
    {
        "name": "read_workbook",
        "description": "Read an .xlsx workbook",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "write_cell",
        "description": "Write one cell",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _serve_fake_mcp(server_in_fd: int, server_out_fd: int) -> None:
    """Bucle de servidor MCP mínimo: initialize + notifications/initialized +
    tools/list. Corre en un hilo con lecturas/escrituras bloqueantes."""
    with (
        os.fdopen(server_in_fd, "rb", buffering=0) as rf,
        os.fdopen(server_out_fd, "wb", buffering=0) as wf,
    ):
        buffer = b""
        while True:
            chunk = rf.read(4096)
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                if not raw.strip():
                    continue
                request = json.loads(raw)
                method = request.get("method")
                if method == "initialize":
                    params = request.get("params") or {}
                    reply = {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "protocolVersion": params.get(
                                "protocolVersion", "2025-06-18"
                            ),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "fake-excel", "version": "1.0.0"},
                        },
                    }
                elif method == "tools/list":
                    reply = {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {"tools": _FAKE_TOOLS},
                    }
                elif "id" not in request:
                    continue  # notificación (notifications/initialized)
                else:
                    reply = {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "error": {"code": -32601, "message": f"no such method {method}"},
                    }
                wf.write((json.dumps(reply) + "\n").encode("utf-8"))
                wf.flush()


def _make_client(timeout_sec: float = 5.0):
    from hermes.mcp.domain.value_objects import Transport
    from hermes.mcp.infrastructure.stdio_mcp_client import StdioMcpClient

    return StdioMcpClient(
        transport=Transport.stdio(["uvx", "excel-mcp-server", "stdio"]),
        timeout_sec=timeout_sec,
    )


async def _handshake_over_pipes() -> list[dict]:
    """Corre el handshake COMPLETO por el camino del launcher y devuelve tools."""
    client_read_fd, server_out_fd = os.pipe()  # servidor -> cliente
    server_in_fd, client_write_fd = os.pipe()  # cliente  -> servidor
    thread = threading.Thread(
        target=_serve_fake_mcp, args=(server_in_fd, server_out_fd), daemon=True
    )
    thread.start()
    client = _make_client()
    client._launcher_read_fd = client_read_fd
    client._launcher_write_fd = client_write_fd
    try:
        streams = client._wire_launcher_streams(client_read_fd, client_write_fd)
        await client._start_session_owner(streams=streams)
        return await client.list_tools()
    finally:
        await client.close()
        thread.join(timeout=5.0)


def _force_sdk2_jsonrpc_shape(monkeypatch) -> None:
    """Deja `mcp.types` con la forma EXACTA del SDK 2.0: `JSONRPCMessage` como
    alias de unión PEP-604 y sin `jsonrpc_message_adapter`."""
    import mcp.types as mcp_types

    union = (
        mcp_types.JSONRPCRequest
        | mcp_types.JSONRPCNotification
        | mcp_types.JSONRPCResponse
        | mcp_types.JSONRPCError
    )
    monkeypatch.setattr(mcp_types, "JSONRPCMessage", union, raising=False)
    monkeypatch.delattr(mcp_types, "jsonrpc_message_adapter", raising=False)


# ---------------------------------------------------------------------------
# (a) el decodificador
# ---------------------------------------------------------------------------


class TestJsonrpcLineDecoder:
    _LINE = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}
    )

    def test_decodes_with_the_installed_sdk_shape(self) -> None:
        from hermes.mcp.infrastructure.stdio_mcp_client import _jsonrpc_line_decoder

        message = _jsonrpc_line_decoder()(self._LINE)
        assert message is not None

    def test_decodes_with_the_sdk2_union_alias(self, monkeypatch) -> None:
        """El caso que rompía: en el SDK 2.0 `JSONRPCMessage` es un
        `types.UnionType`, sin `model_validate_json`."""
        _force_sdk2_jsonrpc_shape(monkeypatch)
        from hermes.mcp.infrastructure.stdio_mcp_client import _jsonrpc_line_decoder

        message = _jsonrpc_line_decoder()(self._LINE)
        assert getattr(message, "id", None) == 1

    def test_decoded_message_is_accepted_by_session_message(self, monkeypatch) -> None:
        """Lo que decodificamos tiene que ser exactamente lo que `SessionMessage`
        acepta — si el SDK vuelve a mover la forma, esto lo caza."""
        _force_sdk2_jsonrpc_shape(monkeypatch)
        from mcp.shared.message import SessionMessage

        from hermes.mcp.infrastructure.stdio_mcp_client import _jsonrpc_line_decoder

        session_message = SessionMessage(_jsonrpc_line_decoder()(self._LINE))
        assert session_message.message is not None


# ---------------------------------------------------------------------------
# (b)(c) handshake real por el camino del launcher
# ---------------------------------------------------------------------------


class TestLauncherBridgeHandshake:
    def test_initialize_and_list_tools_over_launcher_fds(self) -> None:
        """REGRESIÓN del bug: contra el SDK 2.0 (el que lleva la imagen) esto
        expiraba — el pump de lectura no decodificaba NINGUNA línea entrante y
        `initialize()` esperaba una respuesta que ya estaba en el pipe. Contra
        el SDK 1.x pasa antes y después: por eso el guardián portable del fondo
        del asunto es `TestJsonrpcLineDecoder.test_decodes_with_the_sdk2_union_alias`,
        y éste es el de extremo a extremo por el camino real del launcher."""
        tools = asyncio.run(_handshake_over_pipes())
        assert [t["name"] for t in tools] == ["read_workbook", "write_cell"]
