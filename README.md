<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/priscilapower/arcana-os/main/docs/assets/arcana-logo-cyan-dark.svg">
    <img alt="Arcana OS" src="https://raw.githubusercontent.com/priscilapower/arcana-os/main/docs/assets/arcana-logo-cyan-light.svg" width="340">
  </picture>
</p>

<p align="center"><em>The OS that gives your agents a soul.</em></p>

<p align="center">
  <a href="LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache_2.0-0FB5C9?style=flat-square"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-0FB5C9?style=flat-square">
  <a href="https://docs.arcanaos.cloud"><img alt="Docs" src="https://img.shields.io/badge/docs-arcanaos.cloud-E8A23D?style=flat-square"></a>
</p>

---

Arcana OS is a multi-agent system where each AI agent is assigned a **tarot card archetype** that defines its personality, behaviour, and defaults. The card is not cosmetic — it generates the agent's system prompt and sets its temperature. Blend a primary card with modifier cards to tune the mix.

The project ships as two packages — a Python library you build agents with, and a CLI that wraps it for everyday use — plus `arcana-os`, a meta-package that installs both.

```bash
uv tool install arcana-os

arcana init
arcana providers add -p ollama -m hermes-3 -n local
arcana agent create --name researcher --card hermit --model local
arcana run "what are the tradeoffs between RAG and fine-tuning?" --agent researcher --stream
```

---

## The two packages

### [`arcana-core`](packages/arcana-core/README.md) — the Python library

The real product. Card-configured agents that run through a model gateway.

```python
from arcana import Agent, Card
from arcana.models import ConnectionStore, ModelGateway

async with ModelGateway(ConnectionStore()) as gw:
    agent = Agent(
        name="researcher",
        card=Card.HERMIT,               # IX · The Hermit — Researcher / Deep Analyst
        modifier_cards=[Card.EMPRESS],  # blend in the Empress's warmth
        gateway=gw,
        model="ollama/hermes-3",
    )
    result = await agent.run("summarize recent advances in RAG")
```

It includes the card engine and all 22 Major Arcana, the model gateway with adapters for Ollama, Anthropic, and OpenAI-compatible providers, and agent + session persistence. See the [`arcana-core` README](packages/arcana-core/README.md) for the full module map.

### [`arcana-cli`](packages/arcana-cli/README.md) — the command line

A thin Typer wrapper around the library: `init`, `status`, `providers`, `agent`, `run`, and `cards`. Create and manage agents, connect models, and run prompts without writing Python. See the [`arcana-cli` README](packages/arcana-cli/README.md) for every command.

---

## The 22 Major Arcana

| # | Card | Archetype | Temp |
|---|---|---|---|
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

## Installation

The quickest way — no Python or uv required first (the installer bootstraps both):

```bash
curl -LsSf https://arcanaos.cloud/install.sh | sh
```

Already have [uv](https://docs.astral.sh/uv/)? Install directly:

```bash
# As a CLI tool (installs core + CLI)
uv tool install arcana-os

# As a library
uv add arcana-core
```

Requirements: Python 3.11+ (the curl installer fetches a managed one for you). For local models, [Ollama](https://ollama.ai) with your preferred model.

---

## Roadmap

This is the **Phase 1a MVP** — card-configured agents that run statelessly today. The next phase adds federated memory, a tool/MCP gateway, and **The World**, a meta-agent (card XXI) that routes work across agents; their type systems are already modelled in `arcana-core`, ready to be wired up.

---

## License

[Apache License 2.0](LICENSE). Arcana OS is open source and will stay that way; the Apache license also lets us offer a hosted edition in the future.
