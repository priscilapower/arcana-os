# Tools

`arcana.tools` gives agents capabilities beyond text generation. An agent that
is handed a **tool gateway** and a list of **subscriptions** can call tools in a
bounded model→tool→model loop; without them it runs exactly as before.

```python
from arcana import Agent, Card
from arcana.models import ConnectionStore, ModelGateway
from arcana.tools import default_tool_gateway

async with ModelGateway(ConnectionStore()) as gw:
    agent = Agent(
        name="researcher",
        card=Card.HERMIT,
        gateway=gw,
        model="ollama/hermes-3",
        tool_gateway=default_tool_gateway(),                 # builtin tools, ready to run
        tool_subscriptions=["builtin/web_search", "builtin/fetch_url"],
    )
    answer = await agent.run("What changed in RAG this month? Search, then cite sources.")
```

## How it fits together

Three layers with a clean split of responsibility:

- **`MCPRegistry`** owns tool *definitions* — the JSON schemas the model sees.
  Builtins are always registered; external MCP servers are added here too.
- **`ToolGateway`** owns *routing and permission*. It resolves an agent's
  subscriptions into request tools, re-checks permission on every call, applies a
  per-tool timeout, and routes the call to the adapter that can run it. Every
  failure comes back as a `ToolResult(success=False, …)` fed to the model — the
  loop never raises.
- **`ToolAdapter`** owns *execution*. `BuiltinToolAdapter` hosts the builtin
  tools behind one shared HTTP client.

An agent only ever sees the tools it subscribed to, and the gateway denies any
call outside that set — defense-in-depth against a model that names a tool it
was never offered.

## Builtin tools

`default_tool_gateway()` ships two network tools:

- **`web_search(query, max_results?)`** → a normalised list of
  `{title, url, snippet}`. It runs behind a swappable provider: the default is
  **DuckDuckGo**, which needs no API key, so search works out of the box. Brave
  and Tavily are opt-in via [`WebToolsConfig`](#configuration) with a key in the
  environment.
- **`fetch_url(url)`** → the readable text of a page (`{final_url, status_code,
  content_type, text, truncated}`). HTML is reduced to main text; text/JSON pass
  through; unsupported binary types return a typed placeholder.

### The egress safety envelope

`fetch_url` is the first tool whose argument comes from the model and whose
effect leaves the process, so it fails **closed**. Before any request is issued
and again on **every redirect hop**, it enforces:

- a scheme allow-list (`http` / `https` only);
- an SSRF guard that blocks private, loopback, link-local, reserved, and cloud
  **metadata** addresses (`169.254.169.254`, `127.0.0.1`, `10.0.0.0/8`, `::1`, …),
  including hostnames that resolve to them;
- a streamed response byte cap (~2 MiB → `truncated`) and a wall-clock timeout.

A blocked, oversized, or timed-out fetch is a `ToolResult` error the model can
adapt to — never an exception. An HTTP 404 is a *successful* tool result, not a
tool error.

## Configuration

Provider choice, API keys, size/time/redirect caps, and the local-dev
`allow_private_hosts` escape hatch are **operator config only** — a tool argument
can never set them, so a prompt-injected argument cannot widen the egress
surface. Knobs are overridable via `ARCANA_TOOLS_*` environment variables.

```toml
[tools]
fetch_max_bytes     = 2097152     # ~2 MiB
fetch_timeout_s     = 10
max_redirects       = 3
allow_private_hosts = false       # SSRF escape hatch, off by default

[tools.web_search]
provider    = "duckduckgo"        # | "brave" | "tavily"
max_results = 5
user_agent  = "arcana-os/web_search (+https://github.com/priscilapower/arcana-os)"

# Provider endpoints default to each vendor's URL; override to route through a
# corporate proxy / API gateway or a regional endpoint without a code change.
duckduckgo_url = "https://html.duckduckgo.com/html/"
brave_url      = "https://api.search.brave.com/res/v1/web/search"
tavily_url     = "https://api.tavily.com/search"
# BRAVE_API_KEY / TAVILY_API_KEY read from the environment
```

Every knob has an `ARCANA_TOOLS_*` environment variable — for example, to send
search through an internal gateway and identify your deployment:

```bash
export ARCANA_TOOLS_WEB_SEARCH_USER_AGENT="acme-corp-bot/2.0"
export ARCANA_TOOLS_DUCKDUCKGO_SEARCH_URL="https://ddg-proxy.internal/html/"
export ARCANA_TOOLS_BRAVE_SEARCH_URL="https://brave-gw.internal/search"
export ARCANA_TOOLS_TAVILY_SEARCH_URL="https://tavily-gw.internal/search"
```

::: arcana.tools.WebToolsConfig

::: arcana.tools.SearchProviderName

## Gateway

::: arcana.tools.ToolGateway

::: arcana.tools.default_tool_gateway

## Adapters

::: arcana.tools.ToolAdapter

::: arcana.tools.BuiltinToolAdapter

## Registry

::: arcana.tools.MCPRegistry

::: arcana.tools.get_mcp_registry
