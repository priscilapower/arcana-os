# arcana-cli

The `arcana` command-line interface for Arcana OS. A thin Typer wrapper around `arcana-core`.

```bash
pip install arcana-cli
# or inside the monorepo:
uv sync --all-packages --all-extras
```

---

## Commands

### `arcana init`

Initialise Arcana OS. It creates `~/.arcana/` with the default directory layout and `config.json`.

```bash
arcana init
```

### `arcana status`

Show system status: home directory, agent count, World Engine state.

```bash
arcana status
```

### `arcana run`

Run a prompt targeting a specific agent with `--agent`.

```bash
arcana run "Summarise the latest on LLM evals"
arcana run "Refactor this module" --agent my-agent --stream
```

| Flag | Default | Description                           |
|------|---------|---------------------------------------|
| `--agent / -a` | — | Target a specific agent by name       |
| `--stream / -s` | off | Stream output token by token          |

> World routing and agent persistence are Phase 1b. Use the Python API (`arcana-core`) directly for now.

---

### `arcana agent`

Manage agents.

```bash
arcana agent create                          # interactive
arcana agent create --name scout --card the-fool --model ollama/hermes-3
arcana agent list
arcana agent show my-agent
arcana agent delete my-agent
arcana agent delete my-agent --yes           # skip confirmation
```

`arcana agent create` without flags walks you through a card picker that shows all 22 Major Arcana with their archetype and default temperature.

| Subcommand | Description |
|-----------|-------------|
| `create` | Create a new agent (interactive or via flags) |
| `list` | List all agents |
| `show <name>` | Show full config for an agent |
| `delete <name>` | Delete an agent |

---

### `arcana eval`

Run evaluation suites against live agents.

```bash
arcana eval run                              # all suites, rules-only
arcana eval run --fast                       # rules-only (no LLM judge, no cost)
arcana eval run --suite cards                # specific suite
arcana eval run --suite blending --model ollama/hermes-3
arcana eval run --baseline <run-id>          # regression comparison

arcana eval list                             # list all eval cases
arcana eval list --suite cards

arcana eval results <run-id>                 # show a previous run
```

| Flag | Default | Description                                 |
|------|---------|---------------------------------------------|
| `--suite / -s` | all | Suite to run: `cards`, `blending`, or `all` |
| `--fast` | off | Rules-only mode. Fast, free, good for CI    |
| `--baseline / -b` | — | Baseline run ID for regression comparison   |
| `--model / -m` | `ollama/hermes-3` | Model for agents under evaluation           |

CI runs `--fast` on every PR. The full LLM-judge run executes on merge to `main` and requires `ANTHROPIC_API_KEY`. Results are cached to `~/.arcana/evals/results/`.

---

### `arcana connect`

Manage model and MCP connections. *(Phase 1b: coming soon.)*

---

## Runtime layout

All state lives under `~/.arcana/`:

```
~/.arcana/
├── config.json
├── soul.md          ← user-owned context injected into every session
├── agents/{id}/     ← agent.json + memory.db + sessions/
├── connections/     ← model, MCP, memory adapter configs
└── secrets/         ← OS keychain (never plaintext)
```

---

## Development

```bash
# Type check
uv run pyright packages/arcana-cli/arcana_cli

# Tests
uv run pytest packages/arcana-cli/tests/ -v -m "not llm_eval"
```
