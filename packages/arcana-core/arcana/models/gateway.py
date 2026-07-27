"""ModelGateway — single entry point for all model calls.

Owns routing, adapter pooling, retry-with-backoff, error normalization,
and cost metering. Adapters stay dumb — they speak their provider's
protocol and surface normalized ModelError subclasses.
"""

import asyncio
import logging
import random
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from arcana.auth import ApiKeyCredentialProvider, CredentialProvider, OAuthCredentialProvider
from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    ModelAdapter,
    ModelChunk,
    ModelHealth,
)
from arcana.models.connection_store import ConnectionStore, resolve_api_key
from arcana.models.errors import (
    ModelError,
    ModelNotConfiguredError,
    ModelTransientError,
    ModelUnavailableError,
)
from arcana.models.pricing import DEFAULT_PRICING, CostEvent, PricingTable, Usage
from arcana.observability import ModelCallEvent, get_audit_log, get_metrics, get_tracer
from arcana.types.auth import AuthType
from arcana.types.model import ModelConnection, ModelProvider

_log = logging.getLogger(__name__)
_RETRYABLE = (ModelTransientError, ModelUnavailableError)


def _emit_model_call(
    session_id: str,
    model: str,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    attempt: int,
    success: bool,
    error: str | None = None,
) -> None:
    """Append a ModelCallEvent to the audit log and record latency metric. Non-fatal."""
    try:
        event = ModelCallEvent(
            session_id=session_id,
            model=model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            attempt=attempt,
            success=success,
            error=error,
        )
        audit = get_audit_log()
        if audit is not None:
            audit.append(event)
        get_metrics().record_model_call(model=model, latency_ms=latency_ms, success=success)
    except Exception:
        pass


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
    total_timeout: float | None = None

    def backoff(self, attempt: int, *, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return retry_after
        ceiling = min(self.cap, self.base * (self.factor**attempt))
        return random.uniform(0, ceiling)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

_AdapterFactory = Callable[[ModelConnection, "CredentialProvider | None"], ModelAdapter]


@dataclass
class ProviderEntry:
    """Maps a provider string to an adapter factory, endpoint, enum, and key names.

    ``env_var`` / ``provider_key`` are the environment variable and provider-named
    keyring entry the ``api_key`` credential path resolves through (mirroring each
    adapter's precedence); both ``None`` marks a keyless provider (Ollama), for
    which no credential provider is built.
    """

    factory: _AdapterFactory
    default_endpoint: str
    provider: "ModelProvider"
    env_var: str | None = None
    provider_key: str | None = None


def _ollama_factory(conn: ModelConnection, _credentials: "CredentialProvider | None") -> ModelAdapter:
    from arcana.models.adapters.ollama import OllamaAdapter

    return OllamaAdapter(
        model=conn.default_model or "",
        endpoint=conn.endpoint or "http://localhost:11434",
    )


def _anthropic_factory(conn: ModelConnection, credentials: "CredentialProvider | None") -> ModelAdapter:
    from arcana.models.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=conn.default_model or "",
        connection_id=conn.id,
        credentials=credentials,
        auth_type=conn.auth_type,
    )


def _openai_compat_factory(conn: ModelConnection, credentials: "CredentialProvider | None") -> ModelAdapter:
    from arcana.models.adapters.openai_compat import OpenAICompatAdapter

    return OpenAICompatAdapter(
        model=conn.default_model or "",
        base_url=conn.endpoint or "https://api.openai.com/v1",
        credentials=credentials,
    )


def _custom_factory(conn: ModelConnection, credentials: "CredentialProvider | None") -> ModelAdapter:
    from arcana.models.adapters.custom_api import CustomAPIAdapter

    return CustomAPIAdapter(
        model=conn.default_model or "",
        base_url=conn.endpoint,
        credentials=credentials,
    )


# Single source of truth: provider alias → (factory, default endpoint, canonical
# enum, credential key names). Aliases (lmstudio, openai-compat) share a factory
# and all point to OPENAI_COMPAT.
_DEFAULT_ENTRIES: dict[str, ProviderEntry] = {
    "ollama": ProviderEntry(_ollama_factory, "http://localhost:11434", ModelProvider.OLLAMA),
    "anthropic": ProviderEntry(
        _anthropic_factory, "", ModelProvider.ANTHROPIC, env_var="ANTHROPIC_API_KEY", provider_key="anthropic_api_key"
    ),
    "openai": ProviderEntry(
        _openai_compat_factory,
        "https://api.openai.com/v1",
        ModelProvider.OPENAI,
        env_var="OPENAI_API_KEY",
        provider_key="openai_api_key",
    ),
    "lmstudio": ProviderEntry(
        _openai_compat_factory,
        "http://localhost:1234/v1",
        ModelProvider.OPENAI_COMPAT,
        env_var="OPENAI_API_KEY",
        provider_key="openai_api_key",
    ),
    "openai-compat": ProviderEntry(
        _openai_compat_factory,
        "",
        ModelProvider.OPENAI_COMPAT,
        env_var="OPENAI_API_KEY",
        provider_key="openai_api_key",
    ),
    "openai_compat": ProviderEntry(
        _openai_compat_factory,
        "",
        ModelProvider.OPENAI_COMPAT,
        env_var="OPENAI_API_KEY",
        provider_key="openai_api_key",
    ),
    "custom": ProviderEntry(
        _custom_factory, "", ModelProvider.CUSTOM, env_var="CUSTOM_API_KEY", provider_key="custom_api_key"
    ),
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
                f"Register it via ProviderRegistry.register() or add a connection with `arcana providers add`."
            )
        # Derive a *stable* id from provider + endpoint so repeated calls for the
        # same unconfigured provider resolve to one identity — and therefore one
        # pooled adapter (the cache is keyed on connection identity). model_id is
        # deliberately excluded: the adapter is model-agnostic (the request
        # carries model_id), so all models on one provider share the adapter.
        conn_id = uuid5(NAMESPACE_URL, f"arcana:default-connection:{provider}:{entry.default_endpoint}")
        return ModelConnection(
            id=conn_id,
            name=f"{provider}/{model_id}",
            provider=entry.provider,
            default_model=model_id,
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


def _cache_key(provider: str, endpoint: str, connection_id: UUID) -> str:
    """Key the adapter pool on **connection identity**, never the credential.

    A rotating OAuth token must not spawn a new pooled adapter on every refresh
    (nor leak-multiply pool entries), so the key is ``provider:endpoint:id`` —
    the connection's stable identity — rather than a hash of the access token.
    """
    return f"{provider}:{endpoint}:{connection_id}"


def _build_credentials(conn: ModelConnection, entry: ProviderEntry) -> CredentialProvider | None:
    """Build the connection's credential provider from its ``auth_type``.

    ``oauth`` → an :class:`OAuthCredentialProvider` over the keyring token;
    ``api_key`` → an :class:`ApiKeyCredentialProvider` wrapping ``resolve_api_key``
    (the unchanged four-step precedence); a keyless provider (no key names) → no
    provider at all. A ``oauth`` connection missing its config/reference is a
    fail-closed configuration error, surfaced when the connection is first used.
    """
    if conn.auth_type is AuthType.OAUTH:
        if conn.oauth_config is None or not conn.credential_ref:
            raise ModelNotConfiguredError(
                f"Connection {conn.name!r} is auth_type=oauth but has no OAuth config or credential reference. "
                f"Recreate it with `arcana providers add`."
            )
        return OAuthCredentialProvider(
            conn.oauth_config,
            conn.credential_ref,
            reauth_hint=f"arcana providers login {conn.name}",
        )
    if entry.env_var is None and entry.provider_key is None:
        return None  # keyless provider (e.g. Ollama)
    env_var = entry.env_var or ""
    provider_key = entry.provider_key or ""
    return ApiKeyCredentialProvider(lambda: resolve_api_key(conn.id, env_var, provider_key))


# ---------------------------------------------------------------------------
# ModelGateway
# ---------------------------------------------------------------------------


class ModelGateway:
    """Single entry point the Agent uses to talk to any model.

    Routes ``provider/model_id`` strings to the correct adapter, pools
    adapter instances per connection, retries transient failures with
    exponential backoff, and emits a ``CostEvent`` per completed call.

    Token usage is recorded on the session regardless. Per-call cost is
    emitted only if ``on_cost`` is provided at construction; without it,
    no ``CostEvent`` fires.

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

        req = replace(request, model_id=conn.default_model or "")
        session_id = (req.metadata or {}).get("session_id", "")

        with get_tracer().start_as_current_span("model.complete") as span:
            span.set_attribute("arcana.model", model)
            if session_id:
                span.set_attribute("arcana.session_id", session_id)

            last_exc: Exception | None = None
            start = time.monotonic()
            for attempt in range(self._retry.max_retries + 1):
                attempt_start = time.monotonic()
                try:
                    response = await entry.adapter.complete(req)
                    entry.mark_healthy()
                    latency_ms = int((time.monotonic() - attempt_start) * 1000)
                    span.set_attribute("arcana.input_tokens", response.input_tokens)
                    span.set_attribute("arcana.output_tokens", response.output_tokens)
                    span.set_attribute("arcana.attempts", attempt + 1)
                    await self._emit_cost(model, response, conn, req.metadata)
                    _emit_model_call(
                        session_id, model, latency_ms, response.input_tokens, response.output_tokens, attempt + 1, True
                    )
                    return response
                except _RETRYABLE as exc:
                    latency_ms = int((time.monotonic() - attempt_start) * 1000)
                    _emit_model_call(session_id, model, latency_ms, 0, 0, attempt + 1, False, str(exc))
                    last_exc = exc
                    if attempt == self._retry.max_retries:
                        break
                    delay = self._retry.backoff(attempt, retry_after=getattr(exc, "retry_after", None))
                    if self._retry.total_timeout is not None:
                        elapsed = time.monotonic() - start
                        remaining = self._retry.total_timeout - elapsed
                        if remaining <= 0:
                            break
                        delay = min(delay, remaining)
                    await asyncio.sleep(delay)
                except ModelError as exc:
                    span.record_exception(exc)
                    raise

            # Every path that leaves the retry loop without returning or raising
            # assigns last_exc first; the None case is unreachable, but guard it
            # explicitly rather than raise a possibly-None value.
            if last_exc is None:
                raise ModelUnavailableError(f"Connection {model!r} failed without a captured error.")
            span.record_exception(last_exc)
            if isinstance(last_exc, ModelUnavailableError):
                entry.mark_unhealthy()
            raise last_exc

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

        req = replace(request, model_id=conn.default_model or "")
        async with aclosing(self._retry_stream(model, conn, entry, req)) as gen:
            async for chunk in gen:
                yield chunk

    async def supports_tools(self, model: str) -> bool:
        """Whether the adapter behind ``model`` can accept tool calls.

        Lets a caller decide up front whether to pass ``tools=`` at all, so a
        tool-less model never trips the adapter's ``_guard_tools``.
        """
        conn = self.resolve(model)
        entry = await self._get_cache_entry(conn)
        return entry.adapter.supports_tools

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
        """Parse a model reference and return a ModelConnection with default_model set.

        Accepted forms:
        - ``provider/model_id``
        - ``provider:connection_name/model_id``
        - ``provider`` (bare — uses connection's default_model)
        - ``provider:connection_name`` (bare named — uses connection's default_model)

        When a connection name is given, looks it up by name in ConnectionStore and raises
        ValueError if it doesn't exist. Without a name, checks by provider first then falls
        back to ProviderRegistry defaults so out-of-the-box usage requires no config file.
        """
        provider, conn_name, model_id = self._parse_model_string(model)

        if conn_name is not None:
            conn = self._connections.get_by_name(conn_name)
            if conn is None:
                raise ValueError(
                    f"No connection named {conn_name!r} found. "
                    f"Add it with `arcana providers add` or check your connections file."
                )
            effective = model_id or conn.default_model
            if not effective:
                raise ModelNotConfiguredError(
                    f"Connection {conn_name!r} has no default_model and the reference omits model_id. "
                    f"Set a default or use '{provider}:{conn_name}/<model_id>'."
                )
            return conn.model_copy(update={"default_model": effective})

        entry = self._providers.get(provider)
        if entry is not None:
            conn = self._connections.get_by_provider(entry.provider)
            if conn is not None:
                effective = model_id or conn.default_model
                if not effective:
                    raise ModelNotConfiguredError(
                        f"Connection for {provider!r} has no default_model. "
                        f"Specify: '{provider}/<model_id>' or set a default with `arcana providers edit`."
                    )
                return conn.model_copy(update={"default_model": effective})

        if model_id is None:
            raise ModelNotConfiguredError(
                f"No configured connection for {provider!r} and no model_id in reference {model!r}. "
                f"Add a connection with `arcana providers add` or specify: '{provider}/<model_id>'."
            )
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
    def _parse_model_string(model: str) -> tuple[str, str | None, str | None]:
        """Return ``(provider, connection_name_or_None, model_id_or_None)``.

        Accepts:
        - ``provider/model_id``
        - ``provider:name/model_id``
        - ``provider`` (bare — no model_id; resolved from connection's default_model)
        - ``provider:name`` (bare named — no model_id)
        """
        _invalid = (
            f"Invalid model string: {model!r}. "
            f"Expected 'provider/model_id', 'provider:name/model_id', "
            f"'provider', or 'provider:name'."
        )
        if not model:
            raise ValueError(_invalid)
        parts = model.split("/", 1)
        left = parts[0]
        model_id: str | None = parts[1] if len(parts) == 2 else None
        if not left:
            raise ValueError(_invalid)
        if model_id is not None and not model_id:
            raise ValueError(_invalid)
        if ":" in left:
            provider, conn_name = left.split(":", 1)
            if not provider or not conn_name:
                raise ValueError(_invalid)
            return provider, conn_name, model_id
        return left, None, model_id

    async def _get_cache_entry(self, conn: ModelConnection) -> _CacheEntry:
        provider = str(conn.provider)
        endpoint = conn.endpoint
        key = _cache_key(provider, endpoint, conn.id)

        if key not in self._cache_locks:
            self._cache_locks[key] = asyncio.Lock()

        async with self._cache_locks[key]:
            if key not in self._cache:
                entry = self._providers.get(provider)
                if entry is None:
                    raise ValueError(f"No adapter registered for provider: {provider!r}")
                credentials = _build_credentials(conn, entry)
                # Build with empty default_model — request.model_id carries the actual model.
                conn_for_factory = conn.model_copy(update={"default_model": ""})
                adapter = entry.factory(conn_for_factory, credentials)
                await adapter.connect()
                self._cache[key] = _CacheEntry(adapter=adapter)

        return self._cache[key]

    async def _retry_stream(
        self, model: str, conn: ModelConnection, entry: _CacheEntry, request: CompletionRequest
    ) -> AsyncGenerator[ModelChunk, None]:
        session_id = (request.metadata or {}).get("session_id", "")
        last_exc: Exception | None = None
        start = time.monotonic()
        for attempt in range(self._retry.max_retries + 1):
            started = False
            input_tokens = 0
            output_tokens = 0
            total_chars = 0
            attempt_start = time.monotonic()
            try:
                async with aclosing(entry.adapter.stream(request)) as gen:
                    async for chunk in gen:
                        started = True
                        input_tokens += chunk.input_tokens
                        output_tokens += chunk.output_tokens
                        total_chars += len(chunk.text)
                        yield chunk
                entry.mark_healthy()
                latency_ms = int((time.monotonic() - attempt_start) * 1000)
                await self._emit_cost_streaming(
                    model, conn, input_tokens, output_tokens, total_chars, request.metadata
                )
                _emit_model_call(session_id, model, latency_ms, input_tokens, output_tokens, attempt + 1, True)
                return
            except _RETRYABLE as exc:
                if started:
                    raise  # mid-stream — can't replay output
                latency_ms = int((time.monotonic() - attempt_start) * 1000)
                _emit_model_call(session_id, model, latency_ms, 0, 0, attempt + 1, False, str(exc))
                last_exc = exc
                if attempt < self._retry.max_retries:
                    delay = self._retry.backoff(attempt, retry_after=getattr(exc, "retry_after", None))
                    if self._retry.total_timeout is not None:
                        elapsed = time.monotonic() - start
                        remaining = self._retry.total_timeout - elapsed
                        if remaining <= 0:
                            break
                        delay = min(delay, remaining)
                    await asyncio.sleep(delay)
            except ModelError:
                raise

        if isinstance(last_exc, ModelUnavailableError):
            entry.mark_unhealthy()
        if last_exc is not None:
            raise last_exc

    async def _emit_cost(
        self,
        model: str,
        response: CompletionResponse,
        conn: ModelConnection,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        if self._on_cost is None:
            return
        cost = self._pricing.cost(model, response.input_tokens, response.output_tokens, conn)
        usage = Usage.from_tokens(response.input_tokens, response.output_tokens, cost)
        try:
            result = self._on_cost(CostEvent(model=model, usage=usage, priced=cost is not None, metadata=metadata))
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _log.warning("on_cost sink raised; sink errors are non-fatal", exc_info=True)

    async def _emit_cost_streaming(
        self,
        model: str,
        conn: ModelConnection,
        input_tokens: int,
        output_tokens: int,
        total_chars: int,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        if self._on_cost is None:
            return
        estimated = input_tokens == 0 and output_tokens == 0
        if estimated:
            output_tokens = total_chars // 4
        cost = self._pricing.cost(model, input_tokens, output_tokens, conn)
        usage = Usage.from_tokens(input_tokens, output_tokens, cost)
        try:
            result = self._on_cost(
                CostEvent(model=model, usage=usage, estimated=estimated, priced=cost is not None, metadata=metadata)
            )
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            _log.warning("on_cost sink raised; sink errors are non-fatal", exc_info=True)
