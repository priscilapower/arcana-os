"""ModelGateway — single entry point for all model calls.

Owns routing, adapter pooling, retry-with-backoff, error normalization,
and cost metering. Adapters stay dumb — they speak their provider's
protocol and surface normalized ModelError subclasses.
"""

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from typing import Any

from arcana.models.adapters.anthropic import AnthropicAdapter
from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    ModelAdapter,
    ModelChunk,
    ModelHealth,
)
from arcana.models.adapters.custom_api import CustomAPIAdapter
from arcana.models.adapters.ollama import OllamaAdapter
from arcana.models.adapters.openai_compat import OpenAICompatAdapter
from arcana.models.connection_store import ConnectionStore
from arcana.models.errors import (
    ModelError,
    ModelTransientError,
    ModelUnavailableError,
)
from arcana.models.pricing import DEFAULT_PRICING, CostEvent, PricingTable, Usage
from arcana.types.model import ModelConnection, ModelProvider

_log = logging.getLogger(__name__)
_RETRYABLE = (ModelTransientError, ModelUnavailableError)
_UNHEALTHY_COOLDOWN: float = 30.0  # seconds before a half-open probe is allowed

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """Exponential backoff with full jitter. No fallback to a different model."""

    max_retries: int = 3
    base: float = 0.5
    factor: float = 2.0
    cap: float = 8.0

    def backoff(self, attempt: int, *, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return retry_after
        ceiling = min(self.cap, self.base * (self.factor**attempt))
        return random.uniform(0, ceiling)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_AdapterFactory = Callable[[ModelConnection, str | None], ModelAdapter]


@dataclass
class ProviderEntry:
    """Maps a provider string to an adapter factory and its default endpoint."""

    factory: _AdapterFactory
    default_endpoint: str


def _ollama_factory(conn: ModelConnection, _api_key: str | None) -> ModelAdapter:
    return OllamaAdapter(
        model=conn.model_id,
        endpoint=conn.endpoint or "http://localhost:11434",
    )


def _anthropic_factory(conn: ModelConnection, api_key: str | None) -> ModelAdapter:
    return AnthropicAdapter(model=conn.model_id, api_key=api_key)


def _openai_compat_factory(conn: ModelConnection, api_key: str | None) -> ModelAdapter:
    return OpenAICompatAdapter(
        model=conn.model_id,
        base_url=conn.endpoint or "https://api.openai.com/v1",
        api_key=api_key,
    )


def _custom_factory(conn: ModelConnection, api_key: str | None) -> ModelAdapter:
    return CustomAPIAdapter(
        model=conn.model_id,
        base_url=conn.endpoint,
        api_key=api_key,
    )


# Provider string → (factory, default endpoint).
# Aliases (lmstudio, openai-compat) map to the same factory as openai.
_DEFAULT_ENTRIES: dict[str, ProviderEntry] = {
    "ollama": ProviderEntry(_ollama_factory, "http://localhost:11434"),
    "anthropic": ProviderEntry(_anthropic_factory, ""),
    "openai": ProviderEntry(_openai_compat_factory, "https://api.openai.com/v1"),
    "lmstudio": ProviderEntry(_openai_compat_factory, "http://localhost:1234/v1"),
    "openai-compat": ProviderEntry(_openai_compat_factory, ""),
    "openai_compat": ProviderEntry(_openai_compat_factory, ""),
    "custom": ProviderEntry(_custom_factory, ""),
}

# Normalize provider strings to ModelProvider enum values for ConnectionStore lookup.
_PROVIDER_ENUM: dict[str, ModelProvider] = {
    "ollama": ModelProvider("ollama"),
    "anthropic": ModelProvider("anthropic"),
    "openai": ModelProvider("openai"),
    "lmstudio": ModelProvider("openai_compat"),
    "openai-compat": ModelProvider("openai_compat"),
    "openai_compat": ModelProvider("openai_compat"),
    "custom": ModelProvider("custom"),
}


class ProviderRegistry:
    """Maps provider strings to adapter factories.

    Adding a provider = one ``register()`` call; no gateway changes needed.
    """

    def __init__(self, entries: dict[str, ProviderEntry] | None = None) -> None:
        self._entries: dict[str, ProviderEntry] = dict(_DEFAULT_ENTRIES) if entries is None else entries

    def get(self, provider: str) -> ProviderEntry | None:
        return self._entries.get(provider)

    def register(self, provider: str, entry: ProviderEntry) -> None:
        self._entries[provider] = entry

    def build_default_connection(self, provider: str, model_id: str) -> ModelConnection:
        entry = self._entries.get(provider)
        if not entry:
            raise ValueError(
                f"Unknown provider: {provider!r}. "
                f"Register it via ProviderRegistry.register() or add a connection with `arcana connect model`."
            )
        return ModelConnection(
            name=f"{provider}/{model_id}",
            provider=_PROVIDER_ENUM.get(provider, ModelProvider.CUSTOM),
            model_id=model_id,
            endpoint=entry.default_endpoint,
        )


DEFAULT_PROVIDERS = ProviderRegistry()

# ---------------------------------------------------------------------------
# Adapter cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    adapter: ModelAdapter
    healthy: bool = True
    unhealthy_since: float | None = field(default=None, compare=False)

    def is_open(self, cooldown: float) -> bool:
        """True when the entry is in its cooldown window and calls should fast-fail."""
        if self.healthy or self.unhealthy_since is None:
            return False
        return (time.monotonic() - self.unhealthy_since) < cooldown

    def mark_unhealthy(self) -> None:
        self.healthy = False
        self.unhealthy_since = time.monotonic()

    def mark_healthy(self) -> None:
        self.healthy = True
        self.unhealthy_since = None


def _cache_key(provider: str, endpoint: str, api_key: str | None) -> str:
    key_hash = hashlib.sha256((api_key or "").encode()).hexdigest()[:8]
    return f"{provider}:{endpoint}:{key_hash}"


# ---------------------------------------------------------------------------
# ModelGateway
# ---------------------------------------------------------------------------


class ModelGateway:
    """Single entry point the Agent uses to talk to any model.

    Routes ``provider/model_id`` strings to the correct adapter, pools
    adapter instances per connection, retries transient failures with
    exponential backoff, and emits a ``CostEvent`` per completed call.

    Usage::

        async with ModelGateway(connections=ConnectionStore()) as gw:
            response = await gw.complete("ollama/hermes-3", request)

        # Or with a cost sink:
        def record(event: CostEvent) -> None:
            ...

        gw = ModelGateway(connections=store, on_cost=record)
    """

    def __init__(
        self,
        connections: ConnectionStore,
        *,
        providers: ProviderRegistry | None = None,
        retry: RetryPolicy | None = None,
        pricing: PricingTable | None = None,
        on_cost: Callable[[CostEvent], Any] | None = None,
        unhealthy_cooldown: float = _UNHEALTHY_COOLDOWN,
    ) -> None:
        self._connections = connections
        self._providers = providers or DEFAULT_PROVIDERS
        self._retry = retry or RetryPolicy()
        self._pricing = pricing or DEFAULT_PRICING
        self._on_cost = on_cost
        self._unhealthy_cooldown = unhealthy_cooldown
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(self, model: str, request: CompletionRequest) -> CompletionResponse:
        """Dispatch a completion request, retrying transient errors with backoff."""
        conn = self.resolve(model)
        entry = await self._get_cache_entry(conn)

        if entry.is_open(self._unhealthy_cooldown):
            raise ModelUnavailableError(f"Connection {model!r} is in cooldown after repeated failures.")

        req = replace(request, model_id=conn.model_id)
        last_exc: Exception | None = None
        for attempt in range(self._retry.max_retries + 1):
            try:
                response = await entry.adapter.complete(req)
                entry.mark_healthy()
                await self._emit_cost(model, response, conn)
                return response
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt == self._retry.max_retries:
                    break
                delay = self._retry.backoff(attempt, retry_after=getattr(exc, "retry_after", None))
                await asyncio.sleep(delay)
            except ModelError:
                raise

        if isinstance(last_exc, ModelUnavailableError):
            entry.mark_unhealthy()
        raise last_exc  # type: ignore[misc]

    async def stream(self, model: str, request: CompletionRequest) -> AsyncGenerator[ModelChunk, None]:
        """Stream a response as ``ModelChunk`` deltas.

        Retry applies only before the first token arrives — mid-stream failures
        are surfaced immediately since output cannot be cleanly replayed.
        Emits one ``CostEvent`` after the stream completes.
        """
        conn = self.resolve(model)
        entry = await self._get_cache_entry(conn)

        if entry.is_open(self._unhealthy_cooldown):
            raise ModelUnavailableError(f"Connection {model!r} is in cooldown after repeated failures.")

        req = replace(request, model_id=conn.model_id)
        async with aclosing(self._retry_stream(model, conn, entry, req)) as gen:
            async for chunk in gen:
                yield chunk

    async def health(self, model: str | None = None) -> dict[str, ModelHealth]:
        """Check health for one model string or all cached adapters.

        A successful check resets the unhealthy flag so the connection is
        allowed back into the request path without waiting for cooldown.
        """
        if model is not None:
            conn = self.resolve(model)
            entry = await self._get_cache_entry(conn)
            result = await entry.adapter.health_check()
            if result.healthy:
                entry.mark_healthy()
            return {model: result}

        results: dict[str, ModelHealth] = {}
        for key, entry in self._cache.items():
            result = await entry.adapter.health_check()
            if result.healthy:
                entry.mark_healthy()
            results[key] = result
        return results

    def resolve(self, model: str) -> ModelConnection:
        """Parse ``provider/model_id`` and return a ``ModelConnection``.

        Checks ``ConnectionStore`` first; falls back to ``ProviderRegistry``
        defaults so out-of-the-box usage requires no config file.
        """
        provider, model_id = self._parse_model_string(model)
        enum_provider = _PROVIDER_ENUM.get(provider)

        if enum_provider is not None:
            conn = self._connections.get_by_provider(enum_provider)
            if conn is not None:
                return conn.model_copy(update={"model_id": model_id})

        return self._providers.build_default_connection(provider, model_id)

    async def aclose(self) -> None:
        """Close all cached adapters. Called automatically by the context manager."""
        for entry in self._cache.values():
            await entry.adapter.aclose()
        self._cache.clear()
        self._cache_locks.clear()

    async def __aenter__(self) -> "ModelGateway":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_model_string(model: str) -> tuple[str, str]:
        parts = model.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Invalid model string: {model!r}. Expected 'provider/model_id' "
                f"(e.g. 'ollama/hermes-3', 'anthropic/claude-sonnet-4-6')."
            )
        return parts[0], parts[1]

    async def _get_cache_entry(self, conn: ModelConnection) -> _CacheEntry:
        provider = str(conn.provider)
        endpoint = conn.endpoint
        api_key = self._connections.get_api_key(conn.id)
        key = _cache_key(provider, endpoint, api_key)

        if key not in self._cache_locks:
            self._cache_locks[key] = asyncio.Lock()

        async with self._cache_locks[key]:
            if key not in self._cache:
                entry = self._providers.get(provider)
                if entry is None:
                    raise ValueError(f"No adapter registered for provider: {provider!r}")
                # Build with empty model_id — request.model_id carries the actual model.
                conn_for_factory = conn.model_copy(update={"model_id": ""})
                adapter = entry.factory(conn_for_factory, api_key)
                await adapter.connect()
                self._cache[key] = _CacheEntry(adapter=adapter)

        return self._cache[key]

    async def _retry_stream(
        self, model: str, conn: ModelConnection, entry: _CacheEntry, request: CompletionRequest
    ) -> AsyncGenerator[ModelChunk, None]:
        last_exc: Exception | None = None
        for attempt in range(self._retry.max_retries + 1):
            started = False
            input_tokens = 0
            output_tokens = 0
            total_chars = 0
            try:
                async with aclosing(entry.adapter.stream(request)) as gen:
                    async for chunk in gen:
                        started = True
                        input_tokens += chunk.input_tokens
                        output_tokens += chunk.output_tokens
                        total_chars += len(chunk.text)
                        yield chunk
                entry.mark_healthy()
                await self._emit_cost_streaming(model, conn, input_tokens, output_tokens, total_chars)
                return
            except _RETRYABLE as exc:
                if started:
                    raise  # mid-stream — can't replay output
                last_exc = exc
                if attempt < self._retry.max_retries:
                    delay = self._retry.backoff(attempt, retry_after=getattr(exc, "retry_after", None))
                    await asyncio.sleep(delay)
            except ModelError:
                raise

        if isinstance(last_exc, ModelUnavailableError):
            entry.mark_unhealthy()
        if last_exc is not None:
            raise last_exc

    async def _emit_cost(self, model: str, response: CompletionResponse, conn: ModelConnection) -> None:
        if self._on_cost is None:
            return
        cost = self._pricing.cost(model, response.input_tokens, response.output_tokens, conn)
        usage = Usage.from_tokens(response.input_tokens, response.output_tokens, cost)
        try:
            result = self._on_cost(CostEvent(model=model, usage=usage, priced=cost is not None))
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _log.warning("on_cost sink raised; sink errors are non-fatal", exc_info=True)

    async def _emit_cost_streaming(
        self, model: str, conn: ModelConnection, input_tokens: int, output_tokens: int, total_chars: int
    ) -> None:
        if self._on_cost is None:
            return
        estimated = input_tokens == 0 and output_tokens == 0
        if estimated:
            output_tokens = total_chars // 4
        cost = self._pricing.cost(model, input_tokens, output_tokens, conn)
        usage = Usage.from_tokens(input_tokens, output_tokens, cost)
        try:
            result = self._on_cost(CostEvent(model=model, usage=usage, estimated=estimated, priced=cost is not None))
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _log.warning("on_cost sink raised; sink errors are non-fatal", exc_info=True)
