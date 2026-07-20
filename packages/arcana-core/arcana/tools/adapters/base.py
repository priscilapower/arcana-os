"""ToolAdapter ABC and the builtin reference adapter.

A ``ToolAdapter`` is to tools what ``ModelAdapter`` is to models: it declares
the tools it can run (``provides``) and executes a named call against its
backend (``execute``). The ``ToolGateway`` owns routing and permission; an
adapter only knows how to run its own tools.
"""

from abc import ABC, abstractmethod
from typing import Any

from arcana.types.tool import ToolDefinition, ToolResult, ToolType

# A trivial reference builtin: it echoes its argument back, so the tool loop can
# be exercised end-to-end without network or filesystem access.
_ECHO = ToolDefinition(
    name="echo",
    description="Echo back the provided message. A reference tool for exercising the tool loop.",
    input_schema={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
    type=ToolType.BUILTIN,
)


class ToolAdapter(ABC):
    """Every tool backend implements this interface.

    Mirrors ``ModelAdapter``: a ``type`` capability marker, an abstract
    ``provides``/``execute`` pair, and a shared ``supports`` default derived
    from the declared definitions.
    """

    type: ToolType

    @abstractmethod
    def provides(self) -> list[ToolDefinition]:
        """The tool definitions this adapter can execute."""

    def supports(self, name: str) -> bool:
        """True if ``name`` is one of this adapter's tools."""
        return any(d.name == name for d in self.provides())

    @abstractmethod
    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Run tool ``name`` with ``args`` and return a ``ToolResult``."""


class BuiltinToolAdapter(ToolAdapter):
    """Hosts always-available builtin tools.

    Provides a single trivial ``echo`` tool. Custom definitions can be passed in
    to host a different set of builtins behind the same interface.
    """

    type = ToolType.BUILTIN

    def __init__(self, definitions: list[ToolDefinition] | None = None) -> None:
        self._definitions = definitions if definitions is not None else [_ECHO]

    def provides(self) -> list[ToolDefinition]:
        return list(self._definitions)

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name == "echo":
            message = args.get("message", "")
            return ToolResult(tool_name=name, success=True, output=message)
        return ToolResult(tool_name=name, success=False, error=f"unknown builtin tool: {name!r}")
