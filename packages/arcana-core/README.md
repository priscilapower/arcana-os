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
from arcana import Agent, Card
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
| `arcana/memory/` | The federated memory layer. `MemoryFederation` presents private / shared / global tiers as one `MemoryAdapter`: `SQLiteAdapter` (FTS5 keyword search), optional `VectorAdapter` (sqlite-vec semantic + hybrid), and read-only **connectors** — `MarkdownFolderAdapter` makes an Obsidian vault or notes folder searchable from just a path. A **knowledge-graph** layer (`memory_edges` + `EdgeStore`) links notes into typed edges, populated from `[[wikilinks]]` by `WikilinkEdgeExtractor`. Per-tier resilience (timeouts, circuit breakers) and `PRAGMA user_version` migrations round it out. |
| `arcana/context/` | `read_soul()` — loads the optional user-owned `~/.arcana/soul.md` context injected into sessions; missing or unreadable is a silent `None`. |
| `arcana/observability/` | Local-first telemetry: a JSONL `AuditLog` (`~/.arcana/logs/`), OpenTelemetry tracing (optional `[observability]` extra), and metrics — wired through the `Agent` and `ModelGateway`. |
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

Card-configured agents now run on the model gateway with persistent sessions and the **federated memory layer** wired into the run path — an `Agent` given a `MemoryAdapter` recalls and extracts memory across sessions (tiered backends, folder connectors, and a knowledge-graph edge layer — see the module map). Still ahead, as additive work rather than a rewrite: a tool/MCP gateway and **The World** meta-agent (card XXI) that routes work across agents.
