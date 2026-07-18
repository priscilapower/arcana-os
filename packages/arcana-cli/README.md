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

### `arcana providers`

Full lifecycle management for model provider connections.

```bash
arcana providers list
arcana providers add                                          # interactive
arcana providers add -p ollama -m hermes-3 -n local
arcana providers add -p anthropic -m claude-sonnet-4-6 -n claude -k sk-...
arcana providers show local
arcana providers edit local --base-url http://gpu-box:11434
arcana providers edit claude --rotate-key
arcana providers remove local
```

| Subcommand | Description |
|-----------|-------------|
| `list` | List all saved connections |
| `add` | Add a connection (interactive or via flags) |
| `show <name>` | Show a connection's details (secrets redacted) |
| `edit <name>` | Edit base URL, API key, or custom headers |
| `remove <name>` | Remove a connection and its stored credential |

| `providers add` flag | Description |
|------|-------------|
| `--provider / -p` | `ollama`, `anthropic`, `openai`, `openai_compat`, or `custom` |
| `--model-id / -m` | Model ID (e.g. `hermes-3`, `claude-sonnet-4-6`) |
| `--name / -n` | Connection name |
| `--endpoint / -e` | Custom base URL |
| `--api-key / -k` | API key (stored in the OS keyring, never in plaintext) |

| `providers edit` flag | Description |
|------|-------------|
| `--base-url` | New base URL / endpoint |
| `--rotate-key` | Rotate the stored API key (interactive hidden prompt) |
| `--api-key-env VAR` | Read new API key from an environment variable |
| `--header "Key: Value"` | Set a custom header (repeatable; `custom` adapter only) |
| `--no-verify` | Skip the post-edit health check |

| `providers remove` flag | Description |
|------|-------------|
| `--yes / -y` | Skip confirmation prompt |
| `--force` | Remove even if dependent agents exist |

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

The `--model` flag refers to a connection **name** created with `arcana providers add`.

---

### `arcana run`

Run a prompt against a specific agent. `--agent` is required.

```bash
arcana run "Summarise the latest on LLM evals" --agent researcher
arcana run "Refactor this module" --agent my-agent --stream
arcana run "Where did we leave off?" --agent researcher --continue
arcana run "One-off, don't remember this" --agent researcher --no-memory
```

| Flag | Default | Description                      |
|------|---------|----------------------------------|
| `--agent / -a` | — (required) | Target agent by name or UUID |
| `--stream / -s` | off | Stream output token by token     |
| `--session` | new session | Resume a specific session by UUID |
| `--continue` | off | Resume the agent's most recent session |
| `--no-memory` | off | Run stateless — don't load or persist memory |

The agent is rebuilt from its stored record and run through a `ModelGateway` using its configured connection. Each run is recorded to a session under the agent, and — unless `--no-memory` is passed — the agent recalls relevant memory before answering and extracts new memory afterwards through its `MemoryFederation`. The command prints the session id so you can resume it later with `--session` or `--continue`. `--session` and `--continue` are mutually exclusive.

---

### `arcana chat`

Start an interactive, full-screen REPL with a card-configured agent — a scrolling transcript above a pinned input box. It drives the same agent + session + memory path as `arcana run`. `--agent` is required.

```bash
arcana chat --agent researcher
arcana chat --agent researcher --session <uuid>   # resume a session
arcana chat --agent researcher --no-memory        # stateless session
```

| Flag | Default | Description |
|------|---------|-------------|
| `--agent / -a` | — (required) | Target agent by name or UUID |
| `--session` | new session | Resume a specific session by UUID |
| `--no-memory` | off | Run stateless — don't load or persist memory |

Inside the session, slash commands are available (type `/help` to list them):

| Command | Description |
|---------|-------------|
| `/help` | List the in-session commands |
| `/memory` | Show what this agent recalls from this session |
| `/card` | Print the resolved card config — temperature, tone, weights |
| `/switch <name>` | Load another agent in a new session |
| `/retry` | Re-run your last message |
| `/save` | Force a session snapshot to disk now |
| `/clear` | Clear the transcript (history is kept) |
| `/fresh` | Start a new session |
| `/no-memory` | Start a new stateless session (memory off) |
| `/exit` | Close the session and quit |

`Ctrl+C` cancels the current turn (or quits when idle); `Ctrl+D` quits at an empty prompt.

---

### `arcana soul`

Manage `soul.md` — your global user context, injected into every agent's session.

```bash
arcana soul edit   # open in $EDITOR, seeded from a template on first use
arcana soul show   # print the current soul.md
```

| Subcommand | Description |
|-----------|-------------|
| `edit` | Open `soul.md` in `$EDITOR`, creating it from a template on first use |
| `show` | Print the current `soul.md`, or a hint if it doesn't exist |

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
├── soul.md             ← optional global user context (arcana soul edit)
├── agents/{id}/        ← agent.json + memory.db + sessions/
├── connections/        ← models.json
├── vector/             ← global-tier vector store
├── spreads/            ← active agent configurations
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

Agents now run with persistent sessions and the federated memory layer wired into `run` and `chat`. Still to come are the commands whose backends land later — `arcana world`, `arcana spread`, and `arcana mcp` (the tool/MCP gateway and **The World** meta-agent).
