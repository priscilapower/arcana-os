"""Shared test doubles for the tool gateway and builtin tools.

Defined once so the tool test modules stop each re-rolling a slightly different
copy:

* :class:`EchoAdapter` — a trivial in-memory ``ToolAdapter`` that echoes its
  argument back, for exercising gateway routing and the ``Agent`` loop without
  any I/O.
* :func:`make_mock_client` — an ``httpx.AsyncClient`` wired to a ``MockTransport``
  handler, with ``follow_redirects=False`` so the egress guard walks redirects
  itself (matching how ``BuiltinToolAdapter`` builds its real client).
* :data:`PUBLIC_IP` — a public literal host that never triggers DNS and always
  clears the SSRF check, so fetch tests exercise the guard without resolving names.
"""

from typing import Any

import httpx

from arcana.tools.adapters.base import ToolAdapter
from arcana.types.tool import ToolDefinition, ToolResult, ToolType

PUBLIC_IP = "93.184.216.34"


class EchoAdapter(ToolAdapter):
    """In-memory echo tool — a no-I/O fixture for the gateway and Agent loop."""

    type = ToolType.BUILTIN

    def provides(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="echo",
                description="echoes its message back",
                input_schema={
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
                type=ToolType.BUILTIN,
            )
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(tool_name=name, success=True, output=args.get("message", ""))


def make_mock_client(handler: Any) -> httpx.AsyncClient:
    """An AsyncClient backed by ``handler`` via MockTransport; no real network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
