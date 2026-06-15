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
| `arcana connect` | Connect models and providers |
| `arcana soul` | Manage the `soul.md` context file |

## Examples

```bash
# Connect a local Ollama model
arcana connect model -p ollama -m hermes-3 -n local

# Create a Hermit-card agent
arcana agent create --name researcher --card hermit --model local

# Run, streaming the response
arcana run "explain RAG vs fine-tuning" --agent researcher --stream

# Browse the archetypes
arcana cards list
```

!!! note "Generating this page automatically"
    Typer can emit Markdown docs for every command via
    [`typer-cli`](https://typer.tiangolo.com/typer-cli/) (`typer arcana_cli.main
    utils docs`). Once the command surface stabilises, we can wire that into the
    docs build so this page stays in sync with the code automatically.
