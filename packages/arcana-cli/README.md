<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/priscilapower/arcana-os/main/docs/assets/arcana-logo-cyan-dark.svg">
    <img alt="arcana-cli" src="https://raw.githubusercontent.com/priscilapower/arcana-os/main/docs/assets/arcana-logo-cyan-light.svg" width="300">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/priscilapower/arcana-os/blob/main/LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache_2.0-0FB5C9?style=flat-square"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-0FB5C9?style=flat-square">
</p>

# arcana-cli

The `arcana` command-line interface for Arcana OS. A thin Typer wrapper around `arcana-core`.

```bash
uv tool install arcana-cli # or pip install arcana-cli
# or inside the monorepo:
uv sync --all-packages --all-extras
```

---

## Commands

### `arcana init`

Initialise Arcana OS. Creates `~/.arcana/` with the default directory layout, `config.json`, and `world.json`.

```bash
arcana init
```

### `arcana status`

Show system status: home directory, agent count, and model connection count.

```bash
arcana status
```

---

### `arcana connect`

Manage model connections.

```bash
arcana connect model                                   # interactive
arcana connect model -p ollama -m hermes-3 -n local
arcana connect model -p anthropic -m claude-sonnet-4-6 -n claude -k sk-...
arcana connect list
```

| Subcommand | Description |
|-----------|-------------|
| `model` | Add a model connection (interactive or via flags) |
| `list` | List configured connections |

| `connect model` flag | Description |
|------|-------------|
| `--provider / -p` | `ollama`, `anthropic`, `openai`, `openai_compat`, or `custom` |
| `--model-id / -m` | Model ID (e.g. `hermes-3`, `claude-sonnet-4-6`) |
| `--name / -n` | Connection name |
| `--endpoint / -e` | Custom base URL |
| `--api-key / -k` | API key (stored in the OS keyring, never in plaintext) |

---

### `arcana agent`

Manage agents.

```bash
arcana agent create                          # interactive card picker
arcana agent create --name scout --card the-fool --model local
arcana agent list
arcana agent show my-agent
arcana agent edit my-agent --card hermit --tags research,deep
arcana agent delete my-agent
arcana agent delete my-agent --yes           # skip confirmation
```

`arcana agent create` without flags walks you through a card picker showing all 22 Major Arcana with their archetype and default temperature, lets you toggle optional modifier cards, and prints a blend-compatibility summary before saving. The World is reserved and cannot be assigned.

| Subcommand | Description |
|-----------|-------------|
| `create` | Create a new agent (interactive or via flags) |
| `list` | List all agents |
| `show <name>` | Show full config for an agent |
| `edit <name>` | Update name, description, card, model, or tags |
| `delete <name>` | Delete an agent |

The `--model` flag refers to a connection **name** created with `arcana connect model`.

---

### `arcana run`

Run a prompt against a specific agent. `--agent` is required.

```bash
arcana run "Summarise the latest on LLM evals" --agent researcher
arcana run "Refactor this module" --agent my-agent --stream
```

| Flag | Default | Description                      |
|------|---------|----------------------------------|
| `--agent / -a` | — (required) | Target agent by name or UUID |
| `--stream / -s` | off | Stream output token by token     |

The agent is rebuilt from its stored record and run through a `ModelGateway` using its configured connection.

---

### `arcana cards`

Browse the card definitions.

```bash
arcana cards            # list all 22 Major Arcana
arcana cards show hermit
```

| Subcommand | Description |
|-----------|-------------|
| *(default)* | List all 22 Major Arcana |
| `show <name>` | Show one card's archetype, temperature, and details |

---

## Runtime layout

All state lives under `~/.arcana/`, created by `arcana init`:

```
~/.arcana/
├── config.json
├── world.json
├── agents/{id}/        ← agent.json + sessions/
├── connections/        ← models.json
└── cards/{core,custom}/
```

Secrets (API keys) are stored in the OS keyring, never in these files.

---

## Development

```bash
# Type check
uv run pyright packages/arcana-cli/arcana_cli

# Tests
uv run pytest packages/arcana-cli/tests/ -v
```

---

## Roadmap

This is the **Phase 1a MVP** command set. Phase 1b adds the commands whose backends land later — `arcana memory`, `arcana world`, `arcana spread`, `arcana connect mcp`, and an interactive `arcana chat` REPL.
