<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/priscilapower/arcana-os/main/docs/assets/arcana-logo-cyan-dark.svg">
    <img alt="arcana-core" src="https://raw.githubusercontent.com/priscilapower/arcana-os/main/docs/assets/arcana-logo-cyan-light.svg" width="300">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/priscilapower/arcana-os/blob/main/LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache_2.0-0FB5C9?style=flat-square"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-0FB5C9?style=flat-square">
</p>

# arcana-core

The Python library at the heart of Arcana OS. Assign a tarot card to an agent: get a soul.

```bash
pip install arcana-core
# or inside the monorepo:
uv sync --all-packages --all-extras
```

---

## Quick start

Agents run through a `ModelGateway`. The gateway owns connections, routing, retries, and cost metering; the agent just names the model it wants.

```python
from arcana import Agent, BuiltinTool, Card
from arcana.models import ConnectionStore, ModelGateway

async with ModelGateway(ConnectionStore()) as gw:
    agent = Agent(
        name="researcher",
        card=Card.HERMIT,
        gateway=gw,
        model="ollama/hermes-3",
    )

    result = await agent.run("Summarise recent advances in RAG.")
    print(result)

    # Streaming
    async for chunk in agent.stream("What is a vector index?"):
        print(chunk, end="", flush=True)
```

---

## How it works

Every agent is configured by a **tarot card**. The card encodes an archetype: personality and default temperature. The `CardEngine` blends one primary card with optional modifier cards into an `AgentConfig`, which the `Agent` wires together with the gateway.

```
Card enum
  → CardRegistry.get()   → TarotCard  (archetype, traits, prompt ingredients)
  → CardEngine.resolve() → AgentConfig (system_prompt, temperature)
  → Agent.__init__()      ← wires gateway + model + AgentConfig together
  → Agent.run() / Agent.stream()
```

**Blending:** primary card = 70%, modifier cards = 30% split equally. Temperature is linearly blended. `CardEngine.check_compatibility()` reports how well a primary and its modifiers fit together.

```python
agent = Agent(
    name="creative-researcher",
    card=Card.HERMIT,            # 70 % — deep, methodical
    modifier_cards=[Card.FOOL],  # 30 % — curious, action-first
    gateway=gw,
    model="ollama/hermes-3",
)
```

Each run records a `Session` (messages + token totals). Pass an `Agent` a `MemoryAdapter` (typically a `MemoryFederation`) and it recalls relevant memory before answering and extracts new memory afterwards, so continuity carries across sessions — not just within one. Omit the adapter and the agent runs statelessly.

---

## Giving an agent tools

Hand an agent a **tool gateway** and a list of **subscriptions** and its `run()` becomes a bounded model→tool→model loop. Without them it runs exactly as before — tools are off by default.

```python
from uuid import uuid4

from arcana import Agent, BuiltinTool, Card
from arcana.models import ConnectionStore, ModelGateway
from arcana.tools import default_tool_gateway

agent_id = uuid4()

async with ModelGateway(ConnectionStore()) as gw:
    agent = Agent(
        id=agent_id,
        name="researcher",
        card=Card.HERMIT,
        gateway=gw,
        model="ollama/hermes-3",
        # The same id jails the filesystem tools to this agent's own workspace.
        tool_gateway=default_tool_gateway(agent_id),
        tool_subscriptions=[
            BuiltinTool.WEB_SEARCH.qualified,
            BuiltinTool.FETCH_URL.qualified,
            BuiltinTool.READ_FILE.qualified,
        ],
    )
    answer = await agent.run("What changed in RAG this month? Search, then cite sources.")
```

Two builtin tools ship ready to run: **`web_search`** (keyless DuckDuckGo by default; Brave / Tavily opt-in via config + an env key) returning `{title, url, snippet}` results, and **`fetch_url`** returning a page's readable text. Because `fetch_url` takes a model-chosen URL, it fails **closed** behind an egress envelope — an `http`/`https` scheme allow-list, an SSRF guard that blocks private / loopback / cloud-metadata addresses (re-checked on every redirect hop), and response byte + time caps. A blocked, oversized, or timed-out call is a `ToolResult` error the model can adapt to; the loop never raises.

Eight **filesystem** tools ship alongside them. On files: **`list_dir`** (one level, non-recursive, defaulting to the workspace root so an agent can discover what it has), **`read_file`**, **`write_file`** (atomic temp-then-rename, with `create` / `overwrite` / `append` modes), and **`delete_file`** (single files only, soft-deleted into a bounded workspace `.trash/` so an errant delete stays recoverable). On directories: **`make_dir`**, **`move`**, **`copy`**, and **`delete_dir`** (recursive, and soft-deleting the whole subtree into `.trash/` as one entry). They share one path jail: paths are canonicalized before they are allowlisted — so `..` and symlinks cannot escape — the leaf is opened `O_NOFOLLOW` to close the TOCTOU window, non-regular files are refused, and reads and writes are byte-capped. Roots default to `~/.arcana/agents/{id}/workspace/` and never `~/.arcana` itself, so secrets and other agents' memory are outside every default root.

The directory tools raise the stakes in three ways the single-file ones never did, and each fails closed before touching the disk. `move` and `copy` take **two** model-chosen paths, so both are jailed independently and a destination inside its own source is refused. `copy` and `delete_dir` **recurse**, so they walk through a guard that never follows a symlink and re-checks containment per node — closing the `shutil.copytree` / `shutil.rmtree` symlink-follow footgun that would otherwise make a copy an exfiltration primitive and a recursive delete a delete-anything one. And recursion **amplifies**, so tree operations carry aggregate byte, entry, and depth caps. A tree copy is staged beside its destination and renamed in only once complete, so a failed copy leaves no half-built destination and does not destroy what was already there; `delete_dir` refuses to remove a jail root at all.

On top of subscriptions, an agent can carry **guardrails** — declarative rules (`DENY_TOOL`, `DENY_PATTERN`, `SCOPE_PATHS`, `MAX_FILE_SIZE`, `REQUIRE_CONFIRMATION`) resolved once per run from the World's system rules and the agent's own, then evaluated *before* routing, so a blocked write, delete, or command never reaches the adapter. `SCOPE_PATHS` is applied to **every** path argument a tool declares, so `move` and `copy` are checked on their destination as well as their source; `DENY_PATTERN` is a coarse regex tripwire over the shell command a `run_command` call carries. Card archetypes seed the rules: The Hermit is denied every mutating tool. See the [Tools API reference](https://docs.arcanaos.cloud/api/tools/) for the full contract.

---

## The 22 Major Arcana

| # | Card | Archetype | Default temp |
|---|------|-----------|-------------|
| 0 | The Fool | Explorer / Autonomous Agent | 0.95 |
| I | The Magician | Executor / Tool Master | 0.50 |
| II | The High Priestess | Archivist / Pattern Reader | 0.40 |
| III | The Empress | Creator / Generative Agent | 0.85 |
| IV | The Emperor | Orchestrator / System Agent | 0.30 |
| V | The Hierophant | Advisor / Domain Expert | 0.30 |
| VI | The Lovers | Collaborator / Communication | 0.70 |
| VII | The Chariot | Driver / Goal Agent | 0.40 |
| VIII | Strength | Coach / Long-Game Agent | 0.60 |
| IX | The Hermit | Researcher / Deep Analyst | 0.35 |
| X | Wheel of Fortune | Scheduler / Probabilistic | 0.65 |
| XI | Justice | Auditor / Evaluation Agent | 0.20 |
| XII | The Hanged Man | Reframer / Perspective | 0.80 |
| XIII | Death | Transformer / Refactor Agent | 0.40 |
| XIV | Temperance | Integrator / Synthesis | 0.55 |
| XV | The Devil | Shadow / Constraint Breaker | 0.75 |
| XVI | The Tower | Disruptor / Breakthrough | 0.85 |
| XVII | The Star | Companion / Wellbeing Agent | 0.70 |
| XVIII | The Moon | Interpreter / Ambiguity | 0.80 |
| XIX | The Sun | Amplifier / Output Agent | 0.75 |
| XX | Judgement | Reviewer / Reflection | 0.45 |
| XXI | The World | Meta-Agent *(reserved)* | 0.50 |

The World (XXI) is defined but reserved — it cannot be assigned to an agent yet.

---

## Module map

| Module | What's implemented |
|--------|--------------------|
| `arcana/types/` | All Pydantic models — always import from `arcana.types`. Covers cards, agents, sessions, models, memory, tools, and workspaces. |
| `arcana/cards/definitions/` | One file per card, each exporting a `TarotCard` instance (all 22 present). |
| `arcana/cards/registry.py` | `CardRegistry` — `get(Card)`, `all()`. |
| `arcana/cards/engine.py` | `CardEngine` — blending, `resolve()` → `AgentConfig`, `check_compatibility()`. |
| `arcana/agents/agent.py` | `Agent` — `run()` / `stream()`, session recording. |
| `arcana/agents/registry.py` | `AgentRegistry` — CRUD for agent records persisted to `~/.arcana/agents/{id}/agent.json`; `build_runtime()`. |
| `arcana/agents/session_manager.py` | `SessionManager` — session lifecycle, persisted to disk. |
| `arcana/models/` | `ModelGateway` (routing, adapter pooling, retry/backoff, error normalization, cost metering), adapters for Ollama / Anthropic / OpenAI-compatible, `ConnectionStore` (keyring-backed secrets), pricing, and a normalized `ModelError` hierarchy. |
| `arcana/tools/` | The tool gateway. `ToolGateway` resolves an agent's subscriptions, enforces permission + timeout, and routes each call to a `ToolAdapter`; `MCPRegistry` owns the tool definitions. `BuiltinToolAdapter` ships two network tools — **`web_search`** (keyless DuckDuckGo default; Brave / Tavily opt-in) and **`fetch_url`** — behind an SSRF-guarded egress envelope (scheme allow-list, private/metadata-IP blocking re-checked per redirect, byte + time caps), plus eight filesystem tools — **`list_dir`**, **`read_file`**, **`write_file`**, **`delete_file`**, **`make_dir`**, **`move`**, **`copy`**, **`delete_dir`** — behind a path jail (canonicalize-then-allowlist, `O_NOFOLLOW` leaf, byte caps, atomic writes, soft-delete to `.trash/`) rooted at the agent's workspace, with the recursive ones walking a non-following, per-node-contained, aggregate-capped tree walk, plus two execution tools — **`run_code`** (a Python/Bash program) and **`run_command`** (a shell command in the agent's workspace, screened by a coarse `DENY_PATTERN` blocklist) — behind one shared, pluggable, **default-off** sandbox (a soft `setrlimit`/scrubbed-env subprocess default; opt-in `bubblewrap` / `container` backends for real isolation) that spawns nothing until an operator enables it. Guardrail rules are enforced in `dispatch` before any adapter is reached. `MCPToolAdapter` connects any external **MCP server** over SSE or stdio, auto-discovers its tools on connect, and treats it as untrusted: namespaced tool names, rug-pull detection, scoped-env stdio, keyring auth. Every failure returns a `ToolResult` fed back to the model; `run()` never raises. |
| `arcana/memory/` | The federated memory layer. `MemoryFederation` presents private / shared / global tiers as one `MemoryAdapter`: `SQLiteAdapter` (FTS5 keyword search), optional `VectorAdapter` (sqlite-vec semantic + hybrid), and read-only **connectors** — `MarkdownFolderAdapter` makes an Obsidian vault or notes folder searchable from just a path. A **knowledge-graph** layer (`memory_edges` + `EdgeStore`) links notes into typed edges, populated from `[[wikilinks]]` by `WikilinkEdgeExtractor`. Per-tier resilience (timeouts, circuit breakers) and `PRAGMA user_version` migrations round it out. |
| `arcana/context/` | `read_soul()` — loads the optional user-owned `~/.arcana/soul.md` context injected into sessions; missing or unreadable is a silent `None`. |
| `arcana/observability/` | Local-first telemetry: a JSONL `AuditLog` (`~/.arcana/logs/`), OpenTelemetry tracing (optional `[observability]` extra), and metrics — wired through the `Agent` and `ModelGateway`. |
| `arcana/world/` | **The World**'s task router. `WorldEngine.route()` resolves *which agent runs a task* deterministically and offline — a pure `Router.resolve()` layers an explicit bypass, keyword/regex `RoutingRule`s, and a default over the active Spread's candidate pool. Every `RoutingDecision` is written to an append-only, fail-open audit (`~/.arcana/world/routing_audit.jsonl`) before the agent runs; `WorldStore` reads rules/config/Spread from `~/.arcana/`. No model is consulted. |
| `arcana/evals/` | `arcana.evals`, the public evaluation harness: `EvalHarness` runs `EvalCase` suites (cards, memory, decay, blending) scored by a `CompositeJudge` (deterministic `RuleJudge` + optional `LLMJudge`), with JSON result persistence and regression comparison across runs. |

### Types convention

All Pydantic models are re-exported from `arcana.types`. Always import from there:

```python
from arcana.types import Card, AgentConfig  # correct
from arcana.types.card import Card           # avoid
```

---

## Adding a new card

1. Create `arcana/cards/definitions/<name>.py` following `fool.py` — export a single `TarotCard` instance.
2. Import it in `arcana/cards/definitions/__init__.py` and add it to `all_cards()` in canonical order.
3. Add the `Card` enum value to `arcana/types/card.py` if not already present.

---

## Development

```bash
# Lint
uv run ruff check .

# Type check
uv run pyright packages/arcana-core/arcana

# Tests (no LLM calls)
uv run pytest packages/arcana-core/tests/ -v -m "not llm_eval"
```

---

## Roadmap

Card-configured agents now run on the model gateway with persistent sessions, the **federated memory layer** wired into the run path (an `Agent` given a `MemoryAdapter` recalls and extracts memory across sessions — tiered backends, folder connectors, and a knowledge-graph edge layer), and a **tool gateway** with builtin `web_search` and `fetch_url` tools behind an SSRF-guarded egress envelope, filesystem tools behind a per-agent path jail with declarative guardrails enforced before dispatch, plus external **MCP servers** (SSE or stdio) as auto-discovered, fail-closed tool surfaces. **The World** now routes work across agents deterministically (`WorldEngine.route()` — explicit / rule / default resolution with an append-only decision audit). Still ahead, as additive work rather than a rewrite: the rest of the World meta-agent (card XXI) — briefings, cross-agent memory reads, Spread management, and a model-assisted routing tier.
