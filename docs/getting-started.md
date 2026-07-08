# Getting started

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- For local models: [Ollama](https://ollama.ai) with your preferred model

## Install

=== "Quick install"

    No Python or uv needed first — the installer bootstraps both:

    ```bash
    curl -LsSf https://arcanaos.cloud/install.sh | sh
    ```

=== "With uv"

    ```bash
    uv tool install arcana-os
    ```

=== "As a library"

    ```bash
    uv add arcana-core
    ```

=== "Windows"

    In PowerShell — also bootstraps uv + Python:

    ```powershell
    irm https://arcanaos.cloud/install.ps1 | iex
    ```

## Your first agent (CLI)

```bash
arcana init
arcana providers add -p ollama -m hermes-3 -n local
arcana agent create --name researcher --card hermit --model local
arcana run "summarize recent advances in RAG" --agent researcher --stream
```

!!! tip "Agents remember"
    `arcana run` gives each agent a private memory by default, so it recalls
    earlier sessions. Add `--no-memory` to run stateless for a single turn, or
    disable it globally via the `memory` block in `~/.arcana/config.json`. See
    [Memory → Assembling a federation](api/memory.md#assembling-a-federation-for-an-agent).

!!! note "Relocating state"
    Everything Arcana stores, agents, sessions, connections, and secrets, lives
    under `~/.arcana`. Set the `ARCANA_HOME` environment variable to point it
    elsewhere, which is handy for separate profiles, CI, or containers.

## Chat interactively

For a back-and-forth session instead of one-shot `run`, open the chat:

```bash
arcana chat --agent researcher
```

You get a full-screen transcript with streaming replies, slash commands
(`/help`, `/switch`, `/memory`, …), newline and paste editing, and per-agent
history. See [Interactive chat](chat.md) for the full command and key reference.

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
