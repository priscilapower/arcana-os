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

`default_tool_gateway()` ships two network tools, eight filesystem tools — four
acting on files, four on directories — and one code-execution tool that is
**offered but disabled by default**.

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

### File tools

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
  directories are refused (that is `delete_dir`). The file is **moved into a
  workspace `.trash/`** rather than unlinked, so an errant agent delete stays
  recoverable; the trash is bounded and the oldest deletions are pruned. A real
  unlink is opt-in via `hard_delete`.

`read_file`, `write_file`, and `delete_file` operate on **regular files only**:
a directory passed to any of them is refused, and so is a FIFO, socket, or device
node. `list_dir` is the mirror image — it accepts only a directory.

### Directory tools

- **`make_dir(path, parents?, exist_ok?)`** → `{path, created}`. `parents`
  creates missing intermediates, **bounded by `max_depth`** so a pathological
  `a/a/a/…` cannot mint thousands of directories in one call; `exist_ok` turns an
  existing *directory* into a quiet success (an existing file is still refused).
- **`move(src, dst, overwrite?)`** → `{src, dst, moved, atomic}`. Files and trees
  alike. A single `os.rename` when both paths share a filesystem — the normal
  case inside one workspace, and atomic. Across filesystems there is no atomic
  rename to be had, so it degrades to a guarded copy plus a verified delete of
  the source and reports `atomic: false` rather than implying an atomicity it did
  not deliver.
- **`copy(src, dst, overwrite?)`** → `{src, dst, files_copied, dirs_copied, bytes_copied, skipped}`.
  Recursive, and never via `shutil.copytree` (see below).
- **`delete_dir(path)`** → `{path, outcome, entry_count, trash_path?}`. The whole
  subtree is moved into `.trash/` as **one** entry, so a recursive delete stays as
  recoverable as a single-file one. It refuses a **jail or allowlisted root** — a
  workspace's contents can be emptied, the workspace itself cannot be removed out
  from under the jail — and refuses anything that is not a directory.

Directories are still created **implicitly** too: `write_file("notes/2026/q1.md", …)`
makes the intervening directories inside the jail.

### Two paths, and recursion

The directory tools introduce three problems a one-path, one-file tool never had,
and each is a place where the call fails **before** touching the disk:

- **two model-chosen paths.** `move` and `copy` resolve and jail **both** `src`
  and `dst` up front — a two-path operation is only as confined as its weaker
  argument — and refuse a destination **inside** its own source, which would
  otherwise let a copy recurse into its own growing output.
- **recursion crosses the symlink boundary repeatedly.** `copy` and `delete_dir`
  walk their subtree through a guard that **never follows a link**: a symlinked
  directory is copied as a link or unlinked in place, never entered, and every
  other node is re-checked for containment as it is reached. This is what closes
  the `shutil.copytree` / `shutil.rmtree` symlink-follow footgun — those follow
  links by default and re-check nothing per node, which would make `copy` an
  exfiltration primitive and `delete_dir` a delete-anything one. A symlink whose
  target leaves the jail is **skipped** by a copy rather than recreated, and
  reported in `skipped`.
- **amplification.** One call can touch unbounded bytes, files, and depth, none
  of which a per-file byte cap bounds, so tree operations carry **aggregate**
  caps (`max_tree_bytes`, `max_file_count`, `max_depth`) checked *as the walk
  proceeds*.

A tree copy is assembled in a dot-prefixed staging sibling and renamed into place
only once complete, and an existing destination is renamed aside rather than
deleted until the replacement is committed. So a copy that trips a cap, meets an
escaping symlink, or fails part-way leaves **no half-built destination** and does
not take the previous contents down with it.

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

### Code execution

- **`run_code(code, language?, timeout_s?)`** → `{stdout, stderr, exit_code,
  timed_out, truncated}`. `language` defaults to `python` (`bash` is an optional
  second runtime); `timeout_s` may ask for a *shorter* run than the configured
  ceiling, never a longer one. A non-zero `exit_code` is a **successful** tool
  result carrying that code — a program that errors is not a tool failure, the
  same stance `fetch_url` takes on an HTTP 404.

`run_code` is the single highest-blast-radius capability the OS ships: the
argument *is* a program, chosen by a model or injected via an earlier tool
result. So it is **disabled by default** — its schema is offered, but every call
returns `run_code is disabled` and **no process is spawned** until an operator
turns it on. Enabling it, and choosing how strongly it is isolated, is a
deliberate act.

Isolation is a **backend the operator chooses**, not a promise the tool makes:

- **`subprocess`** *(default when enabled)* — the interpreter runs as an isolated
  child (`python -I -S`) in a throwaway workspace, under `setrlimit` CPU/memory/
  file-size/no-core ceilings, in its own session so a timeout kills the whole
  process group, with a **scrubbed environment** built fresh (never the parent's,
  so no secret, keyring handle, or API key is reachable, and `HOME` points at the
  scratch workspace, not your real home). It is honestly a **soft** sandbox — it
  bounds accidents and runaway loops, **not a determined attacker**, and does not
  enforce network isolation.
- **`bubblewrap`** *(recommended on Linux)* — the code runs in a fresh mount and
  network namespace: the host root visible read-only, `$HOME`/`~/.arcana` **not
  bound at all**, and `--unshare-net` when the network is off. Real filesystem and
  network isolation for one external dependency (`bwrap`).
- **`container`** — an ephemeral `--rm` container, `--read-only` with
  `--network=none` and a memory cap, the workspace the only writable mount.
  Strongest isolation, heaviest dependency (a daemon and image), so opt-in.

A backend whose binary is missing (`bwrap`, `docker`) **degrades to "run_code
disabled"** with a clear reason rather than crashing — the same fail-closed
posture as every other guarded condition (disabled, an unknown language, a
timeout, a guardrail block). The sandbox never mounts or reads `~/.arcana`, and
spans record the backend, language, exit code, timing, and output size — **never
the code body or any environment value**.

Because the guardrail seam already gates tools by name, policy does most of the
safety work *before* the sandbox is ever reached: read-only archetypes like The
Hermit ship `DENY_TOOL: run_code`, executing archetypes gate it behind
`REQUIRE_CONFIRMATION` (which denies in an autonomous run with no confirmer), and
`WorldConfig.system_guardrails` can hard-deny it across every agent. Off by
default, denied by most cards, and confirmation-gated for the rest.

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

`SCOPE_PATHS` applies to **every** path argument of a call, not just the first.
Which arguments those are comes from the tool's own definition (`path_args`), so
`move` and `copy` are checked on `dst` as well as `src` — a scope satisfied by
the source alone would let a copy walk the scoped data straight out of it. A tool
that declares no path arguments, including any MCP tool, falls back to the
conventional `path`, so the rule keeps its reach rather than quietly narrowing to
builtins.

A `block` match returns `ToolResult(success=False, error="blocked by guardrail: …")`
carrying the rule's description, so the model can adapt instead of retrying
blindly; `warn` and `log` matches let the call through. Either way a
`GuardrailViolationEvent` lands in the audit log — with the target path but
**never the file contents**.

Card defaults are materialized onto the agent record at creation, so an agent's
constraints are visible in its own `agent.json`. The Hermit ships denied every
mutating tool — `write_file`, `delete_file`, `make_dir`, `move`, `copy`,
`delete_dir`, `run_code` — while still being free to `list_dir` and `read_file`;
The Magician requires confirmation on both deletes and on `run_code`.

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

# Aggregate bounds on one tree operation (copy, recursive delete, cross-fs move).
# The per-file caps above bound one write; these bound the amplification recursion
# adds on top.
max_tree_bytes    = 268435456   # 256 MiB
max_file_count    = 10000       # entries touched by one tree op
max_depth         = 32          # recursion depth, and make_dir(parents=true) levels
```

```bash
# Open up a project directory for an agent that needs to read your repo.
export ARCANA_TOOLS_FS_ALLOWED_ROOTS="/Users/me/projects/report"
export ARCANA_TOOLS_FS_MAX_WRITE_BYTES=1048576
```

Code execution carries its own knobs under `ARCANA_TOOLS_CODE_*`, and `enabled`
is **off by default** — the tool does nothing until an operator turns it on and
picks a backend. `enabled` and `backend` are operator config a tool argument can
never set, so a prompt-injected call can neither enable the sandbox nor weaken it.

```toml
[tools.run_code]
enabled          = false        # OFF by default — nothing runs until enabled
backend          = "subprocess" # | "bubblewrap" | "container"
languages        = ["python"]   # (+ "bash" optional)
timeout_s        = 10           # wall-clock ceiling; a run past it is killed + flagged
mem_limit_mb     = 512          # address-space / memory ceiling for the child
max_output_bytes = 65536        # per-stream cap; output past it is truncated + flagged
network          = false        # best-effort on subprocess; enforced on bwrap/container
container_image  = "python:3-slim"  # container backend only
container_command = "docker"        # | "podman"
```

```bash
# Enable code execution behind real isolation on a Linux host.
export ARCANA_TOOLS_CODE_ENABLED=true
export ARCANA_TOOLS_CODE_BACKEND=bubblewrap
export ARCANA_TOOLS_CODE_LANGUAGES="python,bash"
export ARCANA_TOOLS_CODE_TIMEOUT_S=5
```

::: arcana.tools.WebToolsConfig

::: arcana.tools.SearchProviderName

::: arcana.tools.FsToolsConfig

::: arcana.tools.CodeToolsConfig

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
