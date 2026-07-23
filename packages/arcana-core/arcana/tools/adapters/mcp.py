"""MCPToolAdapter — a live tool surface over an external MCP server.

Where ``BuiltinToolAdapter`` runs tools Arcana ships, this adapter runs tools a
*third party* ships: it opens a session to one MCP server — over **SSE** or
**stdio**, selected by :class:`MCPServerConfig.transport` — discovers that
server's tools, and executes subscribed calls against a live session.

An MCP server is third-party code and third-party context, so the safety
envelope is part of the contract, not an afterthought:

* **Namespace isolation** — every tool is addressed only as ``{server}/{tool}``
  and this adapter owns exactly its ``{cfg.name}/*`` prefix, so a server can
  never shadow ``web_search`` or another builtin.
* **stdio hygiene** — the child is exec'd by argv (never a shell) with a scoped
  environment (a safe base + an explicit allowlist), never the parent's full
  env, so a subprocess is not handed every secret in the process.
* **SSE auth from keyring** — bearer tokens are resolved from the OS keyring at
  connect and injected as headers; they are never persisted, logged, or placed
  on a span. Remote SSE requires ``https``.
* **Untrusted results** — tool output is size-capped and returned as data,
  never interpreted by Arcana itself.
* **Fail closed** — a server that is down, crashes, times out, or returns
  ``isError`` becomes a ``ToolResult(success=False)``; :meth:`execute` never
  raises and a dead server never blocks other adapters or the run.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AsyncExitStack
from typing import Any, Protocol
from urllib.parse import urlsplit

import keyring
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.types import CallToolResult, ContentBlock, EmbeddedResource, ImageContent, ListToolsResult, TextContent
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from arcana.observability import get_current_span
from arcana.tools.adapters.base import ToolAdapter
from arcana.types.tool import (
    MCPServerConfig,
    MCPTransport,
    ToolDefinition,
    ToolResult,
    ToolStatus,
    ToolType,
)

# Keyring service namespace, shared with the model connection store so a user's
# MCP token lives beside their model credentials under one OS keychain entry set.
# Public so the CLI that *writes* an MCP token uses the same namespace this
# adapter *reads* from — the two must never drift.
KEYRING_SERVICE = "arcana"


class MCPToolSettings(BaseSettings):
    """MCP execution knobs, overridable via ``ARCANA_MCP_*`` env vars.

    Read once at import into the module constants below. A per-server config may
    still override the timeout/cap fields; these are the fallback defaults.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_MCP_", extra="ignore")

    # Per-call execution timeout (seconds). A hung tool fails as a ToolResult
    # rather than wedging the run.
    call_timeout_s: float = Field(default=30.0, gt=0.0)
    # Cap on returned tool content (bytes of UTF-8). Third-party results are
    # untrusted; an oversized payload is truncated, never streamed unbounded.
    max_result_bytes: int = Field(default=64_000, gt=0)
    # Ceiling on opening a transport + initialising the session. A server that
    # never completes the handshake fails closed instead of hanging startup.
    connect_timeout_s: float = Field(default=15.0, gt=0.0)


_SETTINGS = MCPToolSettings()

MCP_DEFAULT_CALL_TIMEOUT_S = _SETTINGS.call_timeout_s
MCP_DEFAULT_MAX_RESULT_BYTES = _SETTINGS.max_result_bytes
MCP_CONNECT_TIMEOUT_S = _SETTINGS.connect_timeout_s


class MCPSession(Protocol):
    """The subset of the MCP ``ClientSession`` interface this adapter drives.

    Typing against a structural protocol (rather than the concrete
    ``ClientSession``) lets tests inject an in-memory fake session without a
    real transport, while the real SDK session satisfies it unchanged.
    """

    async def initialize(self) -> Any: ...

    async def list_tools(self) -> ListToolsResult: ...

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult: ...


# A factory opens a transport, wraps it in an MCP session, initialises it, and
# returns the live session together with the AsyncExitStack that owns the
# transport contexts (closing the stack tears the session down).
SessionFactory = Callable[[], Awaitable[tuple[MCPSession, AsyncExitStack]]]


class MCPToolAdapter(ToolAdapter):
    """Runs one MCP server's tools behind the ``ToolAdapter`` interface.

    The adapter connects **lazily on first execution** (or eagerly via
    :meth:`discover`) and holds the session open for reuse. Schema injection is
    connection-free — :meth:`provides` reads the persisted ``discovered_tools``
    — so an agent pays a connect cost only to *execute*, never to *see* tools.
    """

    type = ToolType.MCP

    def __init__(
        self,
        cfg: MCPServerConfig,
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._cfg = cfg
        self._session_factory = session_factory or self._open
        self._session: MCPSession | None = None
        self._stack: AsyncExitStack | None = None
        # One session per server: serialise concurrent call_tool so parallel
        # dispatches to the *same* server don't interleave on one JSON-RPC
        # stream. Distinct servers own distinct adapters and run concurrently.
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._healthy = True

    @property
    def cfg(self) -> MCPServerConfig:
        return self._cfg

    @property
    def healthy(self) -> bool:
        """False once a connect/transport failure has marked this server down."""
        return self._healthy

    def _call_timeout_s(self) -> float:
        return self._cfg.call_timeout_s or MCP_DEFAULT_CALL_TIMEOUT_S

    def _max_result_bytes(self) -> int:
        return self._cfg.max_result_bytes or MCP_DEFAULT_MAX_RESULT_BYTES

    # ------------------------------------------------------------------
    # ToolAdapter interface
    # ------------------------------------------------------------------

    def provides(self) -> list[ToolDefinition]:
        """This server's discovered tools, from the connection-free cache."""
        return list(self._cfg.discovered_tools)

    def supports(self, name: str) -> bool:
        """True for any ``{cfg.name}/<tool>`` — the adapter owns its namespace.

        Claiming the whole prefix (not just currently-discovered tools) means a
        subscribed tool whose server is unreachable still routes here and gets a
        clear ``server unavailable`` error, rather than falling through to a
        generic ``no adapter``.
        """
        server, sep, local = name.partition("/")
        return bool(sep) and bool(local) and server == self._cfg.name

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Run ``{server}/{tool}`` against the live session, fail-closed.

        Strips the server prefix, calls the tool under the per-session lock and
        a timeout, and maps the result's content blocks + ``isError`` into a
        ``ToolResult``. Every failure — unreachable server, timeout, transport
        error, tool-reported error — comes back as a failed ``ToolResult``;
        nothing raises out.
        """
        local = name.split("/", 1)[1] if "/" in name else name

        span = get_current_span()
        span.set_attribute("arcana.mcp.server", self._cfg.name)
        span.set_attribute("arcana.mcp.transport", self._cfg.transport.value)
        span.set_attribute("arcana.mcp.tool", local)

        if self._cfg.ephemeral_stdio:
            return await self._execute_ephemeral(name, local, args, span)
        return await self._execute_persistent(name, local, args, span)

    async def discover(self) -> list[ToolDefinition]:
        """Connect if needed and list the server's tools as ``ToolDefinition``s.

        Returned definitions are ``status=ACTIVE``; the registry runs the
        rug-pull diff against the persisted copies and decides which stay active.
        """
        session = await self._ensure_session()
        resp = await session.list_tools()
        return [_tool_definition(t.name, t.description, t.inputSchema, self._cfg.name) for t in resp.tools]

    async def aclose(self) -> None:
        """Tear down the session and transport if this adapter opened one."""
        await self._reset()

    # ------------------------------------------------------------------
    # Execution paths
    # ------------------------------------------------------------------

    async def _execute_persistent(self, name: str, local: str, args: dict[str, Any], span: Any) -> ToolResult:
        try:
            session = await self._ensure_session()
        except Exception as exc:
            self._healthy = False
            span.set_attribute("arcana.mcp.is_error", True)
            return ToolResult(tool_name=name, success=False, error=f"server unavailable: {type(exc).__name__}")

        try:
            async with self._lock:
                result = await asyncio.wait_for(session.call_tool(local, args or None), timeout=self._call_timeout_s())
        except TimeoutError:
            span.set_attribute("arcana.mcp.is_error", True)
            return ToolResult(tool_name=name, success=False, error="timeout")
        except Exception as exc:
            # Transport/protocol failure: drop the session so the next call
            # reconnects, and fail this call closed (no automatic re-send — the
            # tool may not be idempotent).
            await self._reset()
            self._healthy = False
            span.set_attribute("arcana.mcp.is_error", True)
            return ToolResult(tool_name=name, success=False, error=f"mcp error: {type(exc).__name__}")

        return self._map_result(name, result, span)

    async def _execute_ephemeral(self, name: str, local: str, args: dict[str, Any], span: Any) -> ToolResult:
        """Open a fresh session, run one call, and close — all in this task.

        The escape hatch for servers that don't tolerate a long-lived process.
        Opening and closing within the single execution task also sidesteps
        anyio task-scope constraints entirely.
        """
        try:
            session, stack = await asyncio.wait_for(self._session_factory(), timeout=MCP_CONNECT_TIMEOUT_S)
        except Exception as exc:
            self._healthy = False
            span.set_attribute("arcana.mcp.is_error", True)
            return ToolResult(tool_name=name, success=False, error=f"server unavailable: {type(exc).__name__}")
        try:
            result = await asyncio.wait_for(session.call_tool(local, args or None), timeout=self._call_timeout_s())
        except TimeoutError:
            span.set_attribute("arcana.mcp.is_error", True)
            return ToolResult(tool_name=name, success=False, error="timeout")
        except Exception as exc:
            span.set_attribute("arcana.mcp.is_error", True)
            return ToolResult(tool_name=name, success=False, error=f"mcp error: {type(exc).__name__}")
        finally:
            await _safe_aclose(stack)
        return self._map_result(name, result, span)

    def _map_result(self, name: str, result: CallToolResult, span: Any) -> ToolResult:
        text = _collect_content(result.content, cap=self._max_result_bytes())
        span.set_attribute("arcana.mcp.is_error", bool(result.isError))
        span.set_attribute("arcana.mcp.result_bytes", len(text.encode("utf-8")))
        if result.isError:
            return ToolResult(tool_name=name, success=False, error=text or "tool error")
        return ToolResult(tool_name=name, success=True, output=text)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _ensure_session(self) -> MCPSession:
        """Return the live session, connecting once under a lock if needed."""
        if self._session is not None:
            return self._session
        async with self._connect_lock:
            if self._session is not None:
                return self._session
            session, stack = await asyncio.wait_for(self._session_factory(), timeout=MCP_CONNECT_TIMEOUT_S)
            self._session = session
            self._stack = stack
            self._healthy = True
            return session

    async def _reset(self) -> None:
        """Best-effort teardown; drop the session so the next call reconnects."""
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            await _safe_aclose(stack)

    async def _open(self) -> tuple[MCPSession, AsyncExitStack]:
        """Open the configured transport and initialise an MCP session."""
        stack = AsyncExitStack()
        try:
            if self._cfg.transport is MCPTransport.STDIO:
                read, write = await stack.enter_async_context(stdio_client(self._stdio_params()))
            elif self._cfg.transport is MCPTransport.SSE:
                client = sse_client(self._sse_url(), headers=self._auth_headers())
                read, write = await stack.enter_async_context(client)
            else:
                raise ValueError(f"unsupported MCP transport: {self._cfg.transport}")
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await _safe_aclose(stack)
            raise
        return session, stack

    # ------------------------------------------------------------------
    # Transport hygiene
    # ------------------------------------------------------------------

    def _stdio_params(self) -> StdioServerParameters:
        if not self._cfg.command:
            raise ValueError("stdio transport requires 'command'")
        return StdioServerParameters(command=self._cfg.command, args=list(self._cfg.args), env=self._scoped_env())

    def _scoped_env(self) -> dict[str, str]:
        """A minimal, non-inheriting environment for the stdio child.

        Starts from the SDK's safe base (``PATH``/``HOME``/… only — no API keys
        or arbitrary parent vars), adds the explicitly allowlisted parent vars,
        then the configured literal vars. The parent's full environment is never
        handed to a third-party subprocess.
        """
        env: dict[str, str] = dict(get_default_environment())
        for key in self._cfg.env_allowlist:
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        env.update(self._cfg.env)
        return env

    def _sse_url(self) -> str:
        url = self._cfg.server_url
        if not url:
            raise ValueError("SSE transport requires 'server_url'")
        host = (urlsplit(url).hostname or "").lower()
        # Remote endpoints must be https; loopback may be plain http for local dev.
        if urlsplit(url).scheme != "https" and host not in _LOOPBACK_HOSTS:
            raise ValueError("remote SSE transport requires https")
        return url

    def _auth_headers(self) -> dict[str, Any] | None:
        """Resolve the bearer token from keyring; never persisted or logged."""
        if not self._cfg.auth_key_ref:
            return None
        try:
            token = keyring.get_password(KEYRING_SERVICE, self._cfg.auth_key_ref)
        except Exception:
            token = None
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _tool_definition(name: str, description: str | None, input_schema: dict[str, Any], server: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or "",
        input_schema=input_schema,
        type=ToolType.MCP,
        mcp_server_name=server,
    )


def _collect_content(blocks: Iterable[ContentBlock], *, cap: int) -> str:
    """Join text blocks; summarise non-text blocks; cap to ``cap`` UTF-8 bytes.

    Tool results are untrusted third-party data — returned as text for the
    model, never interpreted by Arcana, and truncated so an oversized payload
    can't blow up context.
    """
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            parts.append(f"[image {block.mimeType} {len(block.data)}B omitted]")
        elif isinstance(block, EmbeddedResource):
            parts.append("[embedded resource omitted]")
        else:
            parts.append(f"[unsupported {getattr(block, 'type', 'unknown')} block]")
    text = "\n".join(parts)
    encoded = text.encode("utf-8")
    if len(encoded) > cap:
        return encoded[:cap].decode("utf-8", "ignore") + "…[truncated]"
    return text


def diff_discovered(
    persisted: Sequence[ToolDefinition],
    fresh: Sequence[ToolDefinition],
) -> list[ToolDefinition]:
    """Fold a fresh discovery against the approved copies, flagging rug-pulls.

    A tool whose ``description`` or ``input_schema`` differs from a previously
    approved (``ACTIVE``) copy — or one still marked ``CHANGED`` from an earlier
    discovery — is returned as ``CHANGED`` so the registry withholds it. A
    first-seen tool is trusted on this initial discovery.
    """
    prior = {t.name: t for t in persisted}
    result: list[ToolDefinition] = []
    for tool in fresh:
        previous = prior.get(tool.name)
        if previous is None:
            result.append(tool)
            continue
        mutated = previous.description != tool.description or previous.input_schema != tool.input_schema
        if mutated or previous.status is ToolStatus.CHANGED:
            result.append(tool.model_copy(update={"status": ToolStatus.CHANGED}))
        else:
            result.append(tool)
    return result


async def _safe_aclose(stack: AsyncExitStack) -> None:
    """Close an exit stack, swallowing teardown errors.

    Transport teardown can raise anyio cancel-scope errors when it happens in a
    task other than the one that opened it. Losing the reference and letting the
    child be reaped is safe; a noisy teardown must not surface as a tool error.
    """
    try:
        await stack.aclose()
    except Exception:
        pass
