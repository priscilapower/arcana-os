# 🌌 Arcana OS

> The OS that gives your agents a soul.

Arcana OS is a multi-agent management system where each AI agent is assigned a **tarot card archetype** that defines its personality, behaviour, memory profile, and tool preferences.

```bash
uv tool install arcana-os

arcana init
arcana agent create --name researcher --card hermit --model ollama/hermes-3
arcana run "what are the tradeoffs between RAG and fine-tuning?" --agent researcher --stream
```

---

## The Idea

Every agent you create gets a **tarot card**. The card is not cosmetic — it generates a system prompt, sets a temperature, configures memory weights, and suggests tools.

```python
from arcana import Agent, Card
from arcana.models import OllamaAdapter

agent = Agent(
    name="researcher",
    card=Card.HERMIT,          # IX · The Hermit — Researcher / Deep Analyst
    model=OllamaAdapter(model="hermes-3"),
)

result = await agent.run("summarize recent advances in RAG")
```

You can blend cards:

```python
agent = Agent(
    name="creative-researcher",
    card=Card.HERMIT,
    modifier_cards=[Card.EMPRESS],  # depth of Hermit + warmth of Empress
    model=OllamaAdapter(model="hermes-3"),
)
# Temperature blends: 0.35 * 0.7 + 0.85 * 0.3 = 0.50
```

**The World** (`Card.WORLD`) is the meta-agent — it sees all other agents, routes tasks, generates briefings, and detects when agents enter error states (reversed cards).

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
| XXI | The World | Meta-Agent | 0.50 |

---

## Memory Federation

Each agent has a **MemoryFederation** — a unified interface over multiple memory backends. Agents don't know which backends are underneath.

```python
from arcana.memory import MemoryFederation, SQLiteAdapter

federation = MemoryFederation(adapters=[
    SQLiteAdapter(path="~/.arcana/agents/researcher/memory.db"),
    # ObsidianAdapter(vault_path="~/Documents/MyVault"),  # coming soon
])
```

| Backend | Purpose |
|---|---|
| SQLite | Always-on structured memory + FTS |
| sqlite-vec | Semantic / vector similarity search |
| Obsidian | Read/write to your vault as .md files |
| Qdrant | Power mode for large memory stores |

---

## Installation

```bash
# As a CLI tool
uv tool install arcana-os

# As a library
uv add arcana-core
```

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/)

For local models: [Ollama](https://ollama.ai) with your preferred model.

---

## Project Structure

```
arcana/
├── packages/
│   ├── arcana-core/     ← Python library (the real product)
│   └── arcana-cli/      ← Typer CLI wrapping core
├── examples/            ← Working examples
├── tests/               ← Test suite
└── docs/                ← Documentation
```

---

## Roadmap

| Phase | What | Status |
|---|---|---|
| Phase 1 | arcana-core + arcana-cli | 🚧 In progress |
| Phase 2 | arcana-server + arcana-ui (React) | Planned |
| Phase 3 | arcana.cloud hosted product | Planned |

See [build roadmap](https://notion.so) for the full 16-week Epic breakdown.

---

## Contributing

This project is in early development. See `CONTRIBUTING.md` (coming soon).

---

## License

MIT
