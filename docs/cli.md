# CLI reference

The `arcana` command is a thin [Typer](https://typer.tiangolo.com/) wrapper
around `arcana-core`.

## Top-level commands

| Command | Description |
|---------|-------------|
| `arcana init` | Initialise the local Arcana workspace |
| `arcana status` | Show workspace, connections, and agents |
| `arcana run` | Run a prompt against an agent |
| `arcana agent` | Create and manage agents |
| `arcana cards` | Browse the 22 Major Arcana |
| `arcana providers` | Manage model provider connections (list/add/show/edit/remove) |
| `arcana soul` | Manage the `soul.md` context file |

## Managing model providers

`arcana providers` is full CRUD for your model provider connections:

| Subcommand | Description |
|------------|-------------|
| `arcana providers list` | List saved connections |
| `arcana providers add` | Add or update a connection (interactive or via flags) |
| `arcana providers show <name>` | Show a connection's details (secrets are never printed) |
| `arcana providers edit <name>` | Edit the endpoint, rotate the API key, or set headers |
| `arcana providers remove <name>` | Remove a connection and its stored credential |

`add` flags: `--provider/-p` (`ollama`, `anthropic`, `openai`, `openai_compat`, `custom`), `--model-id/-m`, `--name/-n`, `--endpoint/-e`, and `--api-key/-k` (stored in the OS keyring, never in plaintext). Run it with no flags for an interactive prompt.

## Examples

```bash
# Add a local Ollama model connection
arcana providers add -p ollama -m hermes-3 -n local

# Create a Hermit-card agent
arcana agent create --name researcher --card hermit --model local

# Run, streaming the response
arcana run "explain RAG vs fine-tuning" --agent researcher --stream

# Continue the agent's most recent session
arcana run "and how does that compare to LoRA?" --agent researcher --continue

# Browse the archetypes
arcana cards list
```

!!! note "Generating this page automatically"
    Typer can emit Markdown docs for every command via
    [`typer-cli`](https://typer.tiangolo.com/typer-cli/) (`typer arcana_cli.main
    utils docs`). Once the command surface stabilises, we can wire that into the
    docs build so this page stays in sync with the code automatically.
