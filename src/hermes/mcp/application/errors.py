"""mcp/application/errors — named exception types for the MCP bounded context."""

from __future__ import annotations


class McpConnectionError(RuntimeError):
    """Transport cannot be established or was lost."""


class McpProtocolFaultError(McpConnectionError):
    """A stdio MCP child emitted a line its transport could not decode.

    Without this, an undecodable line from an incompatible/corrupt child is
    dropped by the MCP SDK's dispatcher (DEBUG-only) and the handshake hangs
    for the full timeout budget before raising a bare, unhelpful
    `TimeoutError` — see stdio_mcp_client._wire_launcher_streams. Carries the
    child's identity, the exception class, and a truncated, redacted preview
    of the offending line (never the raw line — it may echo back a leaked
    credential from a misbehaving child).
    """

    def __init__(self, *, child: str, line_preview: str, cause: BaseException) -> None:
        self.child = child
        self.line_preview = line_preview
        self.cause_type = type(cause).__name__
        super().__init__(
            f"MCP protocol fault from {child!r} ({self.cause_type}): {cause} "
            f"— offending line: {line_preview!r}"
        )


class McpCallError(RuntimeError):
    """The MCP server returned a protocol-level error for a tool call."""


class McpServerNotFoundError(KeyError):
    """No active connection for the requested server_id."""


class McpToolNotFoundError(KeyError):
    """The tool is not exposed by the target server."""
