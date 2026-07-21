"""Configuration for the builtin **web** tools (``web_search`` / ``fetch_url``).

The byte/time/redirect caps, the SSRF escape hatch, and search-provider
selection ship with sensible defaults but are **overridable via environment
variables**, so an operator can tune egress without waiting for a release.
:class:`WebToolsTunables` is a ``pydantic-settings`` model: it reads
``ARCANA_TOOLS_*`` env vars once at import (typed, coerced, and bounds-validated —
a malformed value fails fast) and seeds the module constants below.

:class:`WebToolsConfig` is the per-adapter object those constants default; provider
API keys are read from the environment (``BRAVE_API_KEY`` / ``TAVILY_API_KEY``),
never from tool arguments. Provider, keys, and ``allow_private_hosts`` are
operator config only, so a prompt-injected argument cannot widen the egress surface.
"""

import os
from enum import StrEnum

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SearchProviderName(StrEnum):
    """Which backend ``web_search`` routes to. DuckDuckGo needs no API key."""

    DUCKDUCKGO = "duckduckgo"
    BRAVE = "brave"
    TAVILY = "tavily"


class WebToolsTunables(BaseSettings):
    """Web-tool egress knobs, overridable via ``ARCANA_TOOLS_*`` env vars.

    Seeds :class:`WebToolsConfig`'s field defaults. Shares the ``ARCANA_TOOLS_``
    prefix with the tool-loop tunables; each ignores the other's variables.
    """

    model_config = SettingsConfigDict(env_prefix="ARCANA_TOOLS_", extra="ignore")

    # Per-fetch response byte cap. Streamed and aborted on exceed, marking the
    # result ``truncated`` — bounds memory against a hostile or huge page.
    fetch_max_bytes: int = Field(default=2 * 1024 * 1024, gt=0)  # ~2 MiB

    # Wall-clock timeout for a single fetch (seconds); composes with the
    # gateway's per-dispatch timeout, and the tighter bound wins.
    fetch_timeout_s: float = Field(default=10.0, gt=0.0)

    # Redirect hops to follow. Each hop is re-run through the SSRF guard, so a
    # public URL cannot 302 into a private address; 0 disables following.
    max_redirects: int = Field(default=3, ge=0)

    # SSRF escape hatch for local development. Off by default: with it off, no
    # request to a private / loopback / link-local / reserved address is issued.
    allow_private_hosts: bool = False

    # Connection-pool ceiling for the one shared client (concurrent dispatch).
    max_connections: int = Field(default=10, gt=0)

    # Search provider and the ceiling a model's ``max_results`` is clamped to.
    web_search_provider: SearchProviderName = SearchProviderName.DUCKDUCKGO
    web_search_max_results: int = Field(default=5, ge=1, le=10)

    # User-Agent web_search sends. A descriptive UA keeps keyless endpoints from
    # treating the client as an anonymous bot; override if an endpoint blocks it.
    web_search_user_agent: str = "arcana-os/web_search (+https://github.com/priscilapower/arcana-os)"

    # Provider base URLs. Overridable so an operator can point at a proxy / API
    # gateway or a regional endpoint without a code change (mirrors the model
    # adapters' configurable endpoints); each is bound to its provider's wire shape.
    duckduckgo_search_url: str = "https://html.duckduckgo.com/html/"
    brave_search_url: str = "https://api.search.brave.com/res/v1/web/search"
    tavily_search_url: str = "https://api.tavily.com/search"


WEB_TOOLS_TUNABLES = WebToolsTunables()

DEFAULT_FETCH_MAX_BYTES = WEB_TOOLS_TUNABLES.fetch_max_bytes
DEFAULT_FETCH_TIMEOUT_S = WEB_TOOLS_TUNABLES.fetch_timeout_s
DEFAULT_MAX_REDIRECTS = WEB_TOOLS_TUNABLES.max_redirects
DEFAULT_ALLOW_PRIVATE_HOSTS = WEB_TOOLS_TUNABLES.allow_private_hosts
DEFAULT_MAX_CONNECTIONS = WEB_TOOLS_TUNABLES.max_connections
DEFAULT_WEB_SEARCH_PROVIDER = WEB_TOOLS_TUNABLES.web_search_provider
DEFAULT_WEB_SEARCH_MAX_RESULTS = WEB_TOOLS_TUNABLES.web_search_max_results
DEFAULT_WEB_SEARCH_USER_AGENT = WEB_TOOLS_TUNABLES.web_search_user_agent
DEFAULT_DUCKDUCKGO_SEARCH_URL = WEB_TOOLS_TUNABLES.duckduckgo_search_url
DEFAULT_BRAVE_SEARCH_URL = WEB_TOOLS_TUNABLES.brave_search_url
DEFAULT_TAVILY_SEARCH_URL = WEB_TOOLS_TUNABLES.tavily_search_url


class WebToolsConfig(BaseModel):
    """Per-adapter configuration for the builtin web tools.

    Field defaults come from the env-overridable tunables above; construct with
    explicit values to override, or via :meth:`from_env` to also pull provider
    API keys from the environment. Provider, keys, and ``allow_private_hosts``
    are **operator config only** — a tool argument can never set them, so a
    prompt-injected argument cannot widen the egress surface.
    """

    fetch_max_bytes: int = Field(default=DEFAULT_FETCH_MAX_BYTES, gt=0)
    fetch_timeout_s: float = Field(default=DEFAULT_FETCH_TIMEOUT_S, gt=0.0)
    max_redirects: int = Field(default=DEFAULT_MAX_REDIRECTS, ge=0)
    allow_private_hosts: bool = DEFAULT_ALLOW_PRIVATE_HOSTS
    max_connections: int = Field(default=DEFAULT_MAX_CONNECTIONS, gt=0)
    web_search_provider: SearchProviderName = DEFAULT_WEB_SEARCH_PROVIDER
    web_search_max_results: int = Field(default=DEFAULT_WEB_SEARCH_MAX_RESULTS, ge=1, le=10)
    web_search_user_agent: str = DEFAULT_WEB_SEARCH_USER_AGENT
    duckduckgo_search_url: str = DEFAULT_DUCKDUCKGO_SEARCH_URL
    brave_search_url: str = DEFAULT_BRAVE_SEARCH_URL
    tavily_search_url: str = DEFAULT_TAVILY_SEARCH_URL
    brave_api_key: str | None = None
    tavily_api_key: str | None = None

    @classmethod
    def from_env(cls) -> "WebToolsConfig":
        """Build from the env-backed defaults, reading keyed-provider secrets from env."""
        return cls(
            brave_api_key=os.environ.get("BRAVE_API_KEY"),
            tavily_api_key=os.environ.get("TAVILY_API_KEY"),
        )
