# Getting started

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- For local models: [Ollama](https://ollama.ai) with your preferred model

## Install

=== "As a CLI tool"

    ```bash
    uv tool install arcana-os
    ```

=== "As a library"

    ```bash
    uv add arcana-core
    ```

## Your first agent (CLI)

```bash
arcana init
arcana connect model -p ollama -m hermes-3 -n local
arcana agent create --name researcher --card hermit --model local
arcana run "summarize recent advances in RAG" --agent researcher --stream
```

## Your first agent (Python)

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

!!! tip "Blending cards"
    A primary card sets the archetype; modifier cards tune the mix. The Hermit
    blended with the Empress stays analytical but warms its tone.
