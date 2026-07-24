# Tools

`arcana.tools` gives agents capabilities beyond text generation. An agent that
is handed a **tool gateway** and a list of **subscriptions** can call tools in a
bounded model→tool→model loop; without them it runs exactly as before.

```python
from arcana import Agent, BuiltinTool, Card
from arcana.models import ConnectionStore, ModelGateway
from arcana.tools import default_tool_gateway

async with ModelGateway(ConnectionStore()) as gw:
    agent = Agent(
        name="researcher",
        card=Card.HERMIT,
        gateway=gw,
        model="ollama/hermes-3",
        tool_gateway=default_tool_gateway(),                 # builtin tools, ready to run
        tool_subscriptions=[
            BuiltinTool.WEB_SEARCH.qualified,
            BuiltinTool.FETCH_URL.qualified,
        ],
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
  tools behind one shared HTTP client; `MCPToolAdapter` hosts one external MCP
  server, connecting on demand over SSE or stdio.

An agent only ever sees the tools it subscribed to, and the gateway denies any
call outside that set — defense-in-depth against a model that names a tool it
was never offered.

## Builtin tools

`default_tool_gateway()` ships two network tools and four filesystem tools.

### Network tools

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

### Filesystem tools

- **`list_dir(path?)`** → `{path, entries, truncated}`, each entry
  `{name, kind, size}` with `kind` one of `file`, `dir`, `symlink`, `other`.
  **Not recursive** — subdirectories are named, never descended into. `path`
  defaults to the workspace root, so an agent with no idea what it has can call
  it with no arguments; without it an agent could only ever read paths it was
  handed. A symlink is reported *as* a symlink and never resolved, so a listing
  never implies the jail reaches further than it does.
- **`read_file(path)`** → `{path, text, truncated, encoding}`. Text only: a
  binary or non-UTF-8 file returns a typed error rather than raw bytes.
- **`write_file(path, content, mode?)`** → `{path, bytes_written, mode}`.
  `mode` is `create` (the default — refuses to clobber), `overwrite`, or
  `append`. Writes are **atomic**: the bytes land in a temp file in the same
  directory and are renamed into place, so a crash never leaves a half-written
  file and never truncates the original.
- **`delete_file(path)`** → `{path, outcome, trash_path?}`. Single files only —
  directories are refused. The file is **moved into a workspace `.trash/`**
  rather than unlinked, so an errant agent delete stays recoverable; the trash
  is bounded and the oldest deletions are pruned. A real unlink is opt-in via
  `hard_delete`.

`read_file`, `write_file`, and `delete_file` operate on **regular files only**:
a directory passed to any of them is refused, and so is a FIFO, socket, or device
node. `list_dir` is the mirror image — it accepts only a directory.

Directory *manipulation* is out: no `move`, `copy`, `mkdir`, or recursive delete.
Directories are still created **implicitly** — `write_file("notes/2026/q1.md", …)`
makes the intervening directories inside the jail — but nothing removes them, so
directory structure an agent creates is permanent from its point of view.

### The path jail

These are the first tools whose model-chosen argument is a local path with
effects on disk, so they fail **closed** behind one shared guard:

- **canonicalize, then allowlist** — `realpath` collapses `..` and every symlink,
  and only the *real* path is tested for containment. A prefix check on the raw
  string would be defeated by either;
- **`O_NOFOLLOW` on the leaf** — closes the window where a symlink is swapped in
  between the check and the open (TOCTOU);
- **regular files only** — a FIFO or device node is refused, and the probe is
  non-blocking so a FIFO cannot wedge the call;
- **byte caps** on both reads (truncated and flagged) and writes (refused before
  a byte reaches the disk).

Roots default to the agent's own workspace, `~/.arcana/agents/{id}/workspace/` —
**never `~/.arcana` itself**, so secrets, connection configs, and other agents'
memory sit outside every default root. A relative path is anchored to that
workspace rather than to the process working directory. Pass the agent's id to
`default_tool_gateway(agent_id)` to establish the jail; without one there are no
roots at all and every filesystem call is refused.

## Guardrails

Beyond subscription membership, an agent can carry **guardrail rules** — static,
serializable constraints the gateway evaluates *before* routing, so a blocked
call never reaches the adapter. For a write or a delete, a late check is no
check at all.

Rules layer in one direction only — narrowing, never widening:

| Layer | Set by | Effect |
|---|---|---|
| `WorldConfig.system_guardrails` | operator | hard floor for every agent |
| `Agent.guardrails` | user | narrows further; seeded from the card at creation |
| `CardArchetype.default_guardrails` | card author | the archetype's boundaries |

Four rule types are enforced: `DENY_TOOL` (by qualified name), `SCOPE_PATHS`
(intersected with the adapter's own jail — both must pass), `MAX_FILE_SIZE`
(bounds a write), and `REQUIRE_CONFIRMATION` (asks the registered confirmer, and
**denies when there is none**, so an autonomous run cannot self-approve). A rule
type this seam cannot evaluate blocks rather than passing silently — an operator
who wrote a restriction is owed enforcement or an error, never a no-op.

A `block` match returns `ToolResult(success=False, error="blocked by guardrail: …")`
carrying the rule's description, so the model can adapt instead of retrying
blindly; `warn` and `log` matches let the call through. Either way a
`GuardrailViolationEvent` lands in the audit log — with the target path but
**never the file contents**.

Card defaults are materialized onto the agent record at creation, so an agent's
constraints are visible in its own `agent.json`. The Hermit ships denied
`write_file` / `delete_file` / `run_code`; The Magician requires confirmation on
deletes.

Name the tools through `BuiltinTool` rather than as string literals. A rule that
names a tool nobody spells correctly constrains nothing, silently — the enum is
the one place those names are defined, and `.qualified` produces the
`builtin/…` form a rule expects.

```python
from arcana.types import BuiltinTool, GuardrailRule, GuardrailRuleType

rules = [
    GuardrailRule(
        type=GuardrailRuleType.DENY_TOOL,
        value=[BuiltinTool.WRITE_FILE.qualified, BuiltinTool.DELETE_FILE.qualified],
        description="This agent reads; it does not alter the world.",
    )
]
agent = Agent(..., guardrails=rules)
```

`BuiltinTool` is a `StrEnum`, so a member *is* its wire name: it compares equal
to the bare string, serializes to it, and can be used anywhere the plain name is
expected — including `tool_subscriptions`.

::: arcana.types.tool.BuiltinTool

## MCP servers

Beyond the builtins, an agent can subscribe to tools hosted by any **MCP
server** — Notion, GitHub, a local stdio server, your own. Each configured
server becomes an `MCPToolAdapter` registered alongside the builtin adapter, so
a subscription like `"notion-mcp/search_pages"` runs end-to-end through the same
bounded loop.

- **Auto-discovery.** `registry.discover(cfg)` opens the transport, lists the
  server's tools, and persists them into `MCPServerConfig.discovered_tools`
  (`~/.arcana/connections/mcps.json`). Runtime schema injection then reads that
  cache — an agent pays a connect cost only to *execute* a tool, never to *see*
  one.
- **Two transports.** `transport="sse"` connects to a remote endpoint
  (`server_url`, `https` required off loopback); `transport="stdio"` spawns a
  local server by `command` + `args`.
- **Wire-safe names.** The model sees a provider-legal function name
  (`notion-mcp__search_pages`); the gateway restores the canonical
  `notion-mcp/search_pages` before permission and routing. The qualified name
  is the source of truth everywhere else.

### The MCP threat model

An MCP server is third-party code *and* third-party context, so the adapter
treats it as untrusted by construction:

- **Namespace isolation.** Every MCP tool is addressed only as
  `{server}/{tool}` and each adapter owns exactly its own prefix, so a server
  can never register `web_search` and shadow a builtin.
- **Rug-pull detection.** Each discovery diffs a tool's `description` and
  `input_schema` against the approved copy; any change flips it to
  `status="changed"` and the tool is **withheld from resolution until
  re-approved**, so mutated third-party metadata is never silently injected.
- **stdio hygiene.** The child is exec'd by argv (never a shell) with a scoped
  environment — a safe base plus an explicit `env_allowlist` — never the
  parent's full environment and secrets.
- **SSE auth from keyring.** A bearer token is resolved from the OS keyring by
  reference at connect and sent as a header; it is never written to
  `mcps.json`, logged, or placed on a span.
- **Fail closed.** A server that is down, crashes, times out, or returns
  `isError` becomes a `ToolResult(success=False, …)`; `execute` never raises,
  and a dead server never blocks other adapters or startup. Tool *results* are
  size-capped untrusted content.

MCP execution knobs — per-call timeout, result cap, connect timeout — are
overridable via `ARCANA_MCP_*` environment variables, and a per-server config
may override the timeout and cap.

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

The filesystem tools carry their own knobs under `ARCANA_TOOLS_FS_*`. The one
value a tool argument can never influence is `allowed_roots`: it is the agent's
workspace plus whatever the operator opened up, so a prompt-injected path cannot
widen the jail.

```toml
[tools.fs]
allowed_roots     = []          # extra roots beyond the agent workspace (os.pathsep-separated)
max_read_bytes    = 5242880     # 5 MiB
max_write_bytes   = 5242880     # 5 MiB
max_list_entries  = 1000        # entries per list_dir before truncation
follow_symlinks   = false       # O_NOFOLLOW on the leaf, on by default
hard_delete       = false       # default: soft-delete into the workspace .trash/
trash_max_entries = 100         # oldest deletions pruned past this
```

```bash
# Open up a project directory for an agent that needs to read your repo.
export ARCANA_TOOLS_FS_ALLOWED_ROOTS="/Users/me/projects/report"
export ARCANA_TOOLS_FS_MAX_WRITE_BYTES=1048576
```

::: arcana.tools.WebToolsConfig

::: arcana.tools.SearchProviderName

::: arcana.tools.FsToolsConfig

::: arcana.tools.PathGuard

## Gateway

::: arcana.tools.ToolGateway

::: arcana.tools.default_tool_gateway

::: arcana.tools.resolve_guardrails

::: arcana.tools.ActiveGuardrails

::: arcana.tools.ToolConfirmer

## Adapters

::: arcana.tools.ToolAdapter

::: arcana.tools.BuiltinToolAdapter

::: arcana.tools.MCPToolAdapter

## Registry

::: arcana.tools.MCPRegistry

::: arcana.tools.get_mcp_registry
