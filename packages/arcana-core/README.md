# arcana-core

The Python library at the heart of Arcana OS. Assign a tarot card to an agent: get a soul.

```bash
pip install arcana-core
# or inside the monorepo:
uv sync --all-packages --all-extras
```

---

## Quick start

```python
from arcana.agents.agent import Agent
from arcana.models.adapters.ollama import OllamaAdapter
from arcana.types.card import Card

agent = Agent(
    name="researcher",
    card=Card.HERMIT,
    model=OllamaAdapter(model="hermes-3"),
)

result = await agent.run("Summarise recent advances in RAG.")
print(result)

# Streaming
async for chunk in agent.stream("What is a vector index?"):
    print(chunk, end="", flush=True)
```

---

## How it works

Every agent is configured by a **tarot card**. The card encodes an archetype: personality, default temperature, memory weights, and decay half-lives. The `CardEngine` blends one primary card with optional modifier cards, producing an `AgentConfig` that wires into the `Agent`.

```
Card enum
  → CardRegistry.get()   → TarotCard  (archetype, traits, prompt ingredients)
  → CardEngine.resolve() → AgentConfig (system_prompt, temperature, memory_weights)
  → Agent.__init__()      ← wires model + AgentConfig together
  → Agent.run() / Agent.stream()
```

**Blending:** primary card = 70%, modifier cards = 30% split equally. Temperature, memory weights, and decay half-lives are all linearly blended.

```python
agent = Agent(
    name="creative-researcher",
    card=Card.HERMIT,            # 70 % — deep, methodical
    modifier_cards=[Card.FOOL],  # 30 % — curious, action-first
    model=OllamaAdapter(model="hermes-3"),
)
```

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

---

## Module map

| Module | Contents                                                      |
|--------|---------------------------------------------------------------|
| `arcana/types/` | All Pydantic models — always import from `arcana.types`       |
| `arcana/cards/definitions/` | One file per card, each exports a `TarotCard` instance        |
| `arcana/cards/registry.py` | `CardRegistry` — `get(Card)`, `all()`                         |
| `arcana/cards/engine.py` | `CardEngine` — blending logic, produces `AgentConfig`         |
| `arcana/agents/agent.py` | `Agent` — wires card config + model + optional memory         |
| `arcana/models/adapters/` | `ModelAdapter` ABC + `OllamaAdapter`, `AnthropicAdapter`      |
| `arcana/tools/` | `ToolAdapter` ABC + registry (currently a stub)               |
| `arcana/evals/` | Public eval harness — `EvalHarness`, `CompositeJudge`, suites |

### Types convention

All Pydantic models are re-exported from `arcana.types`. Always import from there:

```python
from arcana.types import Card, AgentConfig, MemoryEntry  # correct
from arcana.types.card import Card                        # avoid
```

---

## Eval harness

`arcana.evals` ships with the library so you can evaluate your own agents. The harness runs `EvalCase` lists against live agents and scores them with a `CompositeJudge` (rule-based checks + optional LLM judge). Results persist to `~/.arcana/evals/results/` as JSON and support regression comparison across runs.

```python
from arcana.evals.harness import EvalHarness

harness = EvalHarness(use_llm=False)           # rules-only, no cost
summary = await harness.run(suite="cards")
print(summary.passed, "/", summary.total)
```

Built-in suites: `cards`, `blending`.

> **Never change prompts in `arcana/evals/fixtures/prompts.py` once they have results.** Add new ones instead — changing existing prompts breaks regression baselines.

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

# Eval harness (rules-only)
uv run pytest packages/arcana-core/tests/test_eval_harness.py -v
```

---

## Current phase

**Phase 1a (MVP) — stateless agents.** Memory Federation, Tool Gateway, and The World Engine are architecturally defined but not yet wired. The `Agent` class accepts a `memory` argument; extraction and injection become active in Phase 1b.
