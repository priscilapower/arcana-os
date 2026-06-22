# Arcana OS

> The OS that gives your agents a soul.

Arcana OS is a multi-agent system where each AI agent is assigned a **tarot card
archetype** that defines its personality, behaviour, and defaults. The card is
not cosmetic, it generates the agent's system prompt, sets its temperature, and
tone. Blend a primary card with modifiers to tune the mix.

The project ships as two packages: a Python library you build agents with, and a
CLI that wraps it for everyday use.

```bash
uv tool install arcana-os

arcana init
arcana providers add -p ollama -m hermes-3 -n local
arcana agent create --name researcher --card hermit --model local
arcana run "what are the tradeoffs between RAG and fine-tuning?" --agent researcher --stream
```

## The two packages

<div class="grid cards" markdown>

-   __`arcana-core`__ — the Python library

    The real product. Card-configured agents that run locally through a model
    gateway. See the [API reference](api/index.md).

-   __`arcana-cli`__ — the command line

    A thin Typer wrapper: `init`, `status`, `providers`, `agent`, `run`, `cards`.
    See the [CLI reference](cli.md).

</div>

## Where to go next

- New here? Start with [Getting started](getting-started.md).
- Want the archetypes? See [The 22 Major Arcana](cards.md).
- Building in Python? Jump to the [API reference](api/index.md).
