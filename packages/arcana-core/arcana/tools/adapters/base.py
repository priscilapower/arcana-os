"""ToolAdapter ABC and the builtin adapter that hosts the network tools.

A ``ToolAdapter`` is to tools what ``ModelAdapter`` is to models: it declares the
tools it can run (``provides``) and executes a named call against its backend
(``execute``). The ``ToolGateway`` owns routing and permission; an adapter only
knows how to run its own tools.

``BuiltinToolAdapter`` holds a small handler table — one entry per network
builtin — plus one shared ``httpx`` client. Every handler returns a populated
``ToolResult`` and never raises: provider, network, timeout, oversize, and SSRF
failures all come back as ``ToolResult(success=False, error=…)`` fed to the model.
"""

from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlsplit

import httpx

from arcana.observability import get_current_span
from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.builtins.web.egress import EgressBlocked, guarded_get
from arcana.tools.builtins.web.extract import extract_text
from arcana.tools.builtins.web.search import SearchProvider, make_search_provider
from arcana.types._utils import JsonValue
from arcana.types.tool import ToolDefinition, ToolResult, ToolType


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
    """Hosts the always-available builtin tools (``web_search`` + ``fetch_url``).

    Schemas come from :data:`BUILTIN_DEFINITIONS`, the same source the registry
    offers the model, so what an agent sees and what this runs cannot drift.
    Config selects the search provider and the egress caps; a keyed provider
    without its key fails fast here, at construction.
    """

    type = ToolType.BUILTIN

    def __init__(
        self,
        config: WebToolsConfig | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        provider: SearchProvider | None = None,
    ) -> None:
        self._cfg = config or WebToolsConfig.from_env()
        self._owns_http = http is None
        # follow_redirects=False: fetch_url walks redirects manually so the SSRF
        # guard re-runs on every hop.
        self._http = http or httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._cfg.fetch_timeout_s,
            limits=httpx.Limits(max_connections=self._cfg.max_connections),
        )
        self._provider = provider or make_search_provider(self._cfg, self._http)
        self._handlers = {
            "web_search": self._web_search,
            "fetch_url": self._fetch_url,
        }

    def provides(self) -> list[ToolDefinition]:
        return [BUILTIN_DEFINITIONS[name] for name in self._handlers]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult(tool_name=name, success=False, error=f"unknown builtin: {name!r}")
        try:
            return await handler(args)
        except Exception as exc:  # defence-in-depth: a handler must never raise out
            return ToolResult(tool_name=name, success=False, error=f"{type(exc).__name__}: {exc}")

    async def aclose(self) -> None:
        """Close the shared HTTP client if this adapter created it."""
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _web_search(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(tool_name="web_search", success=False, error="missing 'query'")

        max_results = self._clamp_max_results(args.get("max_results"))
        span = get_current_span()
        span.set_attribute("arcana.tool.web_search.provider", self._provider.name)
        span.set_attribute("arcana.tool.web_search.query_length", len(query))

        try:
            results = await self._provider.search(query, max_results=max_results)
        except Exception as exc:
            return ToolResult(
                tool_name="web_search",
                success=False,
                error=f"search provider: {type(exc).__name__}: {exc}",
            )

        span.set_attribute("arcana.tool.web_search.result_count", len(results))
        output: list[JsonValue] = [{"title": r["title"], "url": r["url"], "snippet": r["snippet"]} for r in results]
        return ToolResult(tool_name="web_search", success=True, output=output)

    async def _fetch_url(self, args: dict[str, Any]) -> ToolResult:
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return ToolResult(tool_name="fetch_url", success=False, error="missing 'url'")

        span = get_current_span()
        try:
            response = await guarded_get(self._http, url, self._cfg)
        except EgressBlocked as blocked:
            span.set_attribute("arcana.tool.fetch_url.blocked_reason", blocked.reason)
            return ToolResult(tool_name="fetch_url", success=False, error=f"blocked: {blocked.reason}")
        except httpx.TimeoutException:
            return ToolResult(tool_name="fetch_url", success=False, error="timeout")
        except httpx.HTTPError as exc:
            return ToolResult(tool_name="fetch_url", success=False, error=f"fetch failed: {exc}")

        content_type = response.headers.get("content-type", "")
        text = extract_text(response.content, content_type, response.encoding)

        span.set_attribute("arcana.tool.fetch_url.final_host", urlsplit(response.final_url).hostname or "")
        span.set_attribute("arcana.tool.fetch_url.status_code", response.status_code)
        span.set_attribute("arcana.tool.fetch_url.bytes", len(response.content))
        span.set_attribute("arcana.tool.fetch_url.truncated", response.truncated)

        return ToolResult(
            tool_name="fetch_url",
            success=True,
            output={
                "final_url": response.final_url,
                "status_code": response.status_code,
                "content_type": content_type,
                "text": text,
                "truncated": response.truncated,
            },
        )

    def _clamp_max_results(self, requested: Any) -> int:
        """Clamp a model-supplied ``max_results`` to ``[1, configured ceiling]``."""
        ceiling = self._cfg.web_search_max_results
        if not isinstance(requested, int) or isinstance(requested, bool):
            return ceiling
        return max(1, min(requested, ceiling))
