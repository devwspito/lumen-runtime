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
  (b) un fallo de handshake nunca produce un mensaje vacío.
  (c) handshake real (initialize + tools/list) sobre pipes de verdad, a través
      de `_wire_launcher_streams` + `_start_session_owner` — el mismo camino de
      código que usa producción con los fds que llegan por SCM_RIGHTS.
  (d) una línea indescifrable falla el handshake EN CALIENTE (< 1 s) con un
      McpProtocolFaultError tipado, en vez de expirar a los 120 s del
      presupuesto real — ver stdio_mcp_client._initialize_or_fail_fast.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time

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


def _serve_one_garbage_line(server_in_fd: int, server_out_fd: int, garbage: bytes) -> None:
    """Servidor que emite UNA línea indescifrable y luego se queda mudo —
    reproduce el niño corrupto/incompatible del bug real: nunca contesta al
    `initialize` porque su única línea de salida no es JSON-RPC válido."""
    with (
        os.fdopen(server_in_fd, "rb", buffering=0) as rf,
        os.fdopen(server_out_fd, "wb", buffering=0) as wf,
    ):
        wf.write(garbage)
        wf.flush()
        while rf.read(1):  # se traga cualquier request entrante sin contestar
            pass


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


# ---------------------------------------------------------------------------
# (b) el mensaje de fallo nunca es mudo
# ---------------------------------------------------------------------------


class TestHandshakeFailureIsNeverMute:
    """Un `TimeoutError()` desnudo tiene `str()` == "" — interpolarlo dejaba la
    línea `hermes.dbus.mcp_reconnect_failed server=excel: ...:` sin decir NADA,
    ni siquiera la clase. Eso, por sí solo, es un bug de diagnóstico."""

    def test_bare_timeout_error_names_itself_and_the_budget(self) -> None:
        from hermes.mcp.infrastructure.stdio_mcp_client import (
            _describe_handshake_failure,
        )

        described = _describe_handshake_failure(TimeoutError(), 120.0)
        assert described.startswith("TimeoutError:")
        assert "120s" in described
        assert "handshake" in described

    def test_exception_without_message_still_names_its_class(self) -> None:
        from hermes.mcp.infrastructure.stdio_mcp_client import (
            _describe_handshake_failure,
        )

        assert _describe_handshake_failure(RuntimeError(), 30.0) == "RuntimeError"

    def test_exception_with_message_keeps_both_class_and_message(self) -> None:
        from hermes.mcp.infrastructure.stdio_mcp_client import (
            _describe_handshake_failure,
        )

        described = _describe_handshake_failure(ValueError("Connection closed"), 30.0)
        assert described == "ValueError: Connection closed"

    def test_launcher_handshake_timeout_surfaces_a_non_empty_error(self) -> None:
        """De extremo a extremo por el camino del launcher: un servidor que
        arranca pero NUNCA contesta al initialize tiene que producir un
        McpConnectionError legible, no uno acabado en dos puntos."""
        from hermes.mcp.application.errors import McpConnectionError
        from hermes.mcp.infrastructure.stdio_mcp_client import (
            _describe_handshake_failure,
        )

        client_read_fd, server_out_fd = os.pipe()
        server_in_fd, client_write_fd = os.pipe()
        # Servidor mudo: se traga lo que le llega y no contesta jamás. Cierra su
        # extremo de escritura al salir para que el pump de lectura vea EOF (si
        # no, su hilo bloqueante sobreviviría al bucle de eventos).
        stop = threading.Event()

        def _mute_server() -> None:
            with os.fdopen(server_in_fd, "rb", buffering=0) as rf:
                while not stop.is_set():
                    if not rf.read(1):
                        break
            os.close(server_out_fd)

        thread = threading.Thread(target=_mute_server, daemon=True)
        thread.start()
        client = _make_client(timeout_sec=0.5)
        client._launcher_read_fd = client_read_fd
        client._launcher_write_fd = client_write_fd

        async def _run() -> None:
            streams = client._wire_launcher_streams(client_read_fd, client_write_fd)
            try:
                await client._start_session_owner(streams=streams)
            except Exception as exc:  # noqa: BLE001 — se re-envuelve como en producción
                raise McpConnectionError(
                    "StdioMcpClient: handshake failed via launcher for "
                    "['uvx', 'excel-mcp-server', 'stdio']: "
                    + _describe_handshake_failure(exc, client._timeout_sec)
                ) from exc
            finally:
                stop.set()
                await client.close()

        with pytest.raises(McpConnectionError) as caught:
            asyncio.run(_run())
        thread.join(timeout=5.0)
        message = str(caught.value)
        assert not message.rstrip().endswith(":"), message
        assert "TimeoutError: no initialize() response in 0.5s" in message


# ---------------------------------------------------------------------------
# (d) una línea indescifrable falla el handshake EN CALIENTE, no a los 120 s
# ---------------------------------------------------------------------------


class TestProtocolFaultFailsFast:
    """FALLABA ANTES del fix: contra el SDK 2.0 (el de la imagen), el
    `except` de `_wire_launcher_streams` mandaba la excepción pelada al read
    stream, `JSONRPCDispatcher._dispatch` la descartaba a nivel DEBUG (no hay
    `on_stream_exception` enganchado) y `initialize()` se quedaba esperando
    una respuesta que jamás llegaría — hasta agotar el presupuesto REAL de
    120 s. Con el fix, `_wire_launcher_streams` manda un McpProtocolFaultError
    tipado y `_initialize_or_fail_fast` lo corre en carrera contra
    `initialize()` vía `message_handler` (hook idéntico en 1.x y 2.0)."""

    def test_garbage_line_fails_handshake_fast_with_typed_error(self) -> None:
        from hermes.mcp.application.errors import McpProtocolFaultError

        client_read_fd, server_out_fd = os.pipe()
        server_in_fd, client_write_fd = os.pipe()
        garbage = b"not a json-rpc line at all, Bearer sekret-token-1234567890\n"
        thread = threading.Thread(
            target=_serve_one_garbage_line,
            args=(server_in_fd, server_out_fd, garbage),
            daemon=True,
        )
        thread.start()
        # El presupuesto REAL del bug (120s) — si el fix no gana la carrera,
        # este test cuelga 120s en vez de fallar rápido.
        client = _make_client(timeout_sec=120.0)
        client._launcher_read_fd = client_read_fd
        client._launcher_write_fd = client_write_fd

        async def _run() -> McpProtocolFaultError:
            streams = client._wire_launcher_streams(client_read_fd, client_write_fd)
            try:
                await client._start_session_owner(streams=streams)
                raise AssertionError(
                    "expected McpProtocolFaultError, handshake succeeded"
                )
            finally:
                await client.close()

        start = time.monotonic()
        with pytest.raises(McpProtocolFaultError) as caught:
            asyncio.run(_run())
        elapsed = time.monotonic() - start
        thread.join(timeout=5.0)

        assert elapsed < 1.0, (
            f"handshake fail-fast took {elapsed:.2f}s against a 120s budget "
            "— the race against initialize() is not winning"
        )
        fault = caught.value
        assert fault.child == "excel-mcp-server"
        assert fault.cause_type  # names the exception class, never empty
        assert "not a json-rpc line at all" in fault.line_preview
        assert "Bearer" not in fault.line_preview
        assert "sekret-token-1234567890" not in fault.line_preview
        assert "[REDACTED]" in fault.line_preview
        assert str(fault).startswith("MCP protocol fault from 'excel-mcp-server'")
