<!-- Generated from the slash-command registry by packages/arcana-cli/tests/test_docs_reference.py.
     Don't edit by hand: run `ARCANA_RECORD_GOLDENS=1 uv run pytest packages/arcana-cli/tests/test_docs_reference.py` to regenerate. -->
| Command | In the session | `--json` | What it does |
|---|---|---|---|
| `arcana run` | — the session is the conversation: type the prompt, or /switch to the agent first | yes | Run a prompt specifying --agent directly. |
| `arcana chat` | — it opens the session you are already in | — | Start an interactive session with a card-configured agent. |
| `arcana init` | — a session only opens once ~/.arcana exists, so there is nothing left to initialise | yes | Initialise Arcana OS — creates ~/.arcana/ and sets up The World. |
| `arcana status` | `/status` | yes | Show full system status — agents, connections, The World. |
| `arcana agent create` | `/agent create` | yes | Create a new agent. |
| `arcana agent list` | `/agent list` | yes | List all registered agents. |
| `arcana agent show` | `/agent show` | yes | Show full config for an agent. |
| `arcana agent edit` | `/agent edit` | yes | Edit an agent's name, description, card, model, or tags. |
| `arcana agent delete` | `/agent delete` | yes | Delete an agent (soft-delete). |
| `arcana cards` | `/cards` | yes | Browse the 22 Major Arcana card definitions. |
| `arcana cards show` | `/cards show` | yes | Show full card details — prompt ingredients, memory weights, synergies. |
| `arcana mcp add` | `/mcp add` | yes | Register an MCP server, discover its tools, and persist them. |
| `arcana mcp list` | `/mcp list` | yes | List registered MCP servers. |
| `arcana mcp show` | `/mcp show` | yes | Show a server's detail and discovered tools. |
| `arcana mcp refresh` | `/mcp refresh` | yes | Re-discover a server's tools and re-run the changed-tool diff. |
| `arcana mcp login` | `/mcp login` | yes | Re-run OAuth sign-in for an existing MCP server. |
| `arcana mcp approve` | `/mcp approve` | yes | Re-approve changed tools, admitting their new metadata (the trust gate). |
| `arcana mcp remove` | `/mcp remove` | yes | Remove an MCP server, its discovered tools, and any keyring credential. |
| `arcana memory list` | `/memory list` | yes | List entries by importance — from an agent's memory or a connector (offline). |
| `arcana memory search` | `/memory search` | yes | Search an agent's memory or a connector. |
| `arcana memory inspect` | `/memory inspect` | yes | Show one entry in full, with its decay factor and effective importance. |
| `arcana memory forget` | `/memory forget` | yes | Delete one entry. |
| `arcana memory adapters` | `/memory adapters` | yes | List registered knowledge connectors and probe each one's health. |
| `arcana memory export` | `/memory export` | yes | Export memory to a git-diffable Markdown document (read-only). |
| `arcana memory connect obsidian` | `/memory connect obsidian` | yes | Register an Obsidian vault as an external read-only knowledge connector. |
| `arcana memory connect markdown` | `/memory connect markdown` | yes | Register a plain folder of Markdown notes as an external read-only knowledge connector. |
| `arcana providers list` | `/providers list` | yes | List all saved model provider connections. |
| `arcana providers add` | `/providers add` | yes | Add or update a model provider connection. |
| `arcana providers login` | `/providers login` | yes | Re-run OAuth sign-in for an existing connection. |
| `arcana providers show` | `/providers show` | yes | Show a connection's details. |
| `arcana providers edit` | `/providers edit` | yes | Edit an existing model connection's mutable fields. |
| `arcana providers remove` | `/providers remove` | yes | Remove a model provider connection and its stored credential. |
| `arcana soul edit` | — it hands the whole terminal to $EDITOR | — | Open soul.md in $EDITOR, creating it from a template on first use. |
| `arcana soul show` | `/soul show` | yes | Print the current soul.md, or a hint if it doesn't exist. |
| `arcana tools list` | `/tools list` | yes | List subscribable tools (builtins + discovered MCP tools). |
| `arcana tools subscribe` | `/tools subscribe` | yes | Subscribe an agent to a tool, or to a whole MCP server via ``<server>``/``<server>/*``. |
| `arcana tools unsubscribe` | `/tools unsubscribe` | yes | Unsubscribe an agent from a tool (or a whole-server ``<server>``/``<server>/*``). |
| `arcana world route` | `/world route` | yes | Resolve which agent would run a prompt, without running it (dry run). |
