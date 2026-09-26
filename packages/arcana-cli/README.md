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
arcana providers add -p anthropic -m claude-sonnet-4-6 -n claude \
  --oauth --issuer https://auth.example.com                   # sign in instead of pasting a key
arcana providers show local
arcana providers edit local --base-url http://gpu-box:11434
arcana providers edit claude --rotate-key
arcana providers remove local
```

A connection authenticates by either a static **API key** or **OAuth 2.1**
sign-in, recorded on an `auth_type` discriminator. The API-key path is the
default for model providers (pass `--api-key`/`--api-key-env`, or answer the
prompt); `--oauth --issuer <metadata-url>` runs a browser (or `--device`)
sign-in and stores the resulting token in the OS keyring. `show` surfaces the
`auth_type` and, for OAuth, the token's expiry — never the token itself.

| Subcommand | Description |
|-----------|-------------|
| `list` | List all saved connections |
| `add` | Add a connection (interactive or via flags) |
| `login <name>` | Re-run OAuth sign-in for an existing connection (refresh an expired token) |
| `show <name>` | Show a connection's details (secrets redacted) |
| `edit <name>` | Edit base URL, API key, or custom headers |
| `remove <name>` | Remove a connection and its stored credential |

When an OAuth connection's token expires and can no longer be refreshed, run
`arcana providers login <name>` (not `add`) — it reuses the connection's stored
issuer/client and just refreshes the keyring token in place.

| `providers add` flag | Description |
|------|-------------|
| `--provider / -p` | `ollama`, `anthropic`, `openai`, `openai_compat`, or `custom` |
| `--model-id / -m` | Model ID (e.g. `hermes-3`, `claude-sonnet-4-6`) |
| `--name / -n` | Connection name |
| `--endpoint / -e` | Custom base URL |
| `--api-key / -k` | API key (stored in the OS keyring, never in plaintext) |
| `--api-key-env VAR` | Read the API key from an environment variable |
| `--oauth` | Sign in with OAuth 2.1 instead of pasting a key (requires `--issuer`) |
| `--issuer` | OAuth issuer / metadata base URL |
| `--scope` | OAuth scope to request (repeatable) |
| `--device` | Use the device-code grant (headless / no browser) |
| `--yes / -y` | Overwrite an existing connection of the same name without asking |

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

`arcana agent create` without flags walks you through a two-pane card picker — the Major Arcana beside a live preview of the highlighted card (archetype, default temperature, prompt ingredients) — lets you toggle optional modifier cards, and prints a blend-compatibility summary before saving. In the picker, type to filter, `↑`/`↓` to move, `Enter` to pick, `Space` to toggle a modifier (up to the limit), `Esc` or `Ctrl+C` to cancel. The World is reserved: the picker never offers it. The picker needs a terminal; with stdin or stdout piped, `agent create` / `agent edit` exit with code `1` and ask for `--card`. Without `--model`, `create` then lists your provider connections by number (Enter keeps the first) and asks for a model ID on the one you pick, blank for the connection's default; the last entry, *Other*, takes a whole model reference such as `anthropic/claude-sonnet-4-6` instead.

| Subcommand | Description |
|-----------|-------------|
| `create` | Create a new agent (interactive or via flags) |
| `list` | List all agents |
| `show <name>` | Show full config for an agent |
| `edit <name>` | Update name, description, card, model, or tags |
| `delete <name>` | Delete an agent |

The `--model` flag refers to a connection **name** created with `arcana providers add`.

---

### `arcana mcp`

Register and manage external [MCP](https://modelcontextprotocol.io) servers, and
re-approve tools whose third-party schema has changed. Read commands work offline
from persisted state; only `add` / `refresh` need the server reachable.

```bash
arcana mcp add --name notion-mcp --url https://mcp.notion.com/mcp   # OAuth auto-detected → sign in
arcana mcp add --name notion-mcp --url https://mcp.notion.com/sse \
  --header "Authorization=Bearer $NOTION_TOKEN"          # opt into a static token → keyring
arcana mcp add --name local-mcp --command my-server --arg --stdio
arcana mcp list
arcana mcp show notion-mcp
arcana mcp refresh notion-mcp                 # re-discover + diff
arcana mcp approve notion-mcp --all           # trust changed tools' new schema
arcana mcp approve notion-mcp --tool notion-mcp/search_pages
arcana mcp remove notion-mcp --force          # scans dependent agents first
```

| Subcommand | Description |
|-----------|-------------|
| `add` | Register a server, discover its tools, and persist them (transport inferred) |
| `list` | List servers with transport, tool count, and status |
| `show <name>` | Server detail + discovered tools (auth reference redacted) |
| `refresh <name>` | Re-discover tools; a mutated schema is flagged `changed` and withheld |
| `approve <name>` | Re-approve `changed` tools (`--tool <qn>` repeatable, or `--all`) |
| `remove <name>` | Remove a server, its tools, and any keyring credential |

`add` infers the transport (`--url` → HTTP/SSE, `--command` → stdio). An HTTP/SSE
server **defaults to OAuth 2.1 sign-in** when it advertises OAuth (detected from
the server's Protected Resource Metadata) or with `--oauth`/`--issuer`;
`--header`/`--auth-key` opt into a static bearer, and a server that advertises
neither is added unauthenticated. stdio servers keep their scoped-env-var auth.
Auth material goes to the OS keyring — `mcps.json` stores only a reference, and a
token is never echoed, logged, or written to disk.

| `mcp add` flag | Description |
|------|-------------|
| `--name / -n` | Server name (e.g. `notion-mcp`) |
| `--url` | HTTP/SSE endpoint URL |
| `--command` | stdio server command (implies `--transport stdio`) |
| `--arg` | stdio command argument (repeatable) |
| `--transport` | `http`, `sse`, or `stdio` (inferred from `--url` / `--command`) |
| `--oauth` | Sign in with OAuth (default when the server advertises it) |
| `--issuer` | OAuth issuer / metadata base (skips 401/PRM auto-detect) |
| `--scope` | OAuth scope to request (repeatable) |
| `--device` | Use the device-code grant (headless / no browser) |
| `--header` | Static auth `Authorization=Bearer <token>` — stored in the keyring |
| `--auth-key` | Existing keyring reference holding the bearer token |

A tool whose description or input schema changes since it was first trusted is
marked `changed` and withheld from agents until you `approve` it — the human
checkpoint on a third-party rug-pull. `remove` prints which agents lose which
tools and aborts unless `--force` is given (`--yes` skips the confirmation).

---

### `arcana tools`

Inspect the subscribable tool inventory (builtins + discovered MCP tools) and
manage a per-agent subscription list.

```bash
arcana tools list
arcana tools list --agent researcher          # adds a subscribed ✓ column
arcana tools subscribe researcher builtin/web_search
arcana tools subscribe researcher notion-mcp/search_pages
arcana tools subscribe researcher notion-mcp    # whole server → stored as notion-mcp/*
arcana tools unsubscribe researcher notion-mcp/search_pages --yes
```

| Subcommand | Description |
|-----------|-------------|
| `list` | List subscribable tools; `--agent <name>` marks which are subscribed |
| `subscribe <agent> <qualified_name>` | Subscribe an agent to a tool (validated) |
| `unsubscribe <agent> <qualified_name>` | Remove a subscription |

**Whole-server subscriptions.** Pass a bare server name (`notion-mcp`) or an
explicit wildcard (`notion-mcp/*`) to subscribe an agent to *every* active tool
on that server. It's stored as `notion-mcp/*` and expanded at session start, so
tools discovered later flow in automatically (and `changed`/unapproved tools stay
excluded until you `approve` them). `unsubscribe researcher notion-mcp` removes it.

`subscribe` validates the name against the live registry: an unknown tool exits
`2`, and a `changed`/unapproved MCP tool exits `3` (pointing you at `arcana mcp
approve`). If the agent's model can't accept tool calls, the subscription still
writes but warns that the tools won't be injected.

---

### `arcana memory`

Inspect, audit, forget, connect, and export an agent's memory. Agent memory lives
in binary SQLite under `~/.arcana`; this group makes it visible, exportable, and
*forgettable*. Reads are offline-first — `list`, `inspect`, `adapters`, `export`,
and `search --mode keyword` work with no embedding provider.

```bash
arcana memory list --agent hermit                      # entries by importance (offline)
arcana memory list --agent hermit --scope global       # a different memory tier
arcana memory search "rate limit" --agent hermit       # semantic; falls back to keyword
arcana memory inspect <memory-id> --agent hermit       # detail + decay factor
arcana memory forget <memory-id> --agent hermit        # confirm, then hard-delete
arcana memory forget <memory-id> --agent hermit --archive --yes   # soft-delete
arcana memory connect obsidian --vault ~/notes/vault --name notes # register an external source
arcana memory list --connector notes                   # read that connector's notes
arcana memory adapters                                 # list connectors + health
arcana memory export --agent hermit --out mem.md       # git-diffable Markdown export
```

| Subcommand | Description |
|-----------|-------------|
| `list` | List entries by decayed importance from an agent (`--agent`, `--scope`, `--pool`) **or** a connector (`--connector`); plus `--type`, `--limit`, `--min-importance` |
| `search <query>` | Search an agent's memory or a `--connector` (`--mode semantic\|hybrid\|keyword`); degrades to keyword with no embedder |
| `inspect <id>` | One entry in full, with decay factor and effective importance |
| `forget <id>` | Delete one entry; confirms unless `--yes`, hard-deletes unless `--archive` |
| `connect obsidian \| markdown` | Register a folder of notes as an external, read-only **knowledge connector** |
| `adapters` | List registered knowledge connectors and probe each one's health |
| `export` | Dump memory to Markdown (`--agent`, `--pool`, or `--all`; `--out <file>` or stdout) |

**Connectors vs. pools.** A **knowledge connector** is an external, read-only folder
of notes (an Obsidian vault or markdown dir), addressed by its own name via
`--connector <name>` — it is deliberately kept out of the shared-pool namespace, so
`--pool` always means genuine writable inter-agent memory and never a reference
folder. A connector is never mounted as an agent memory tier; `list`/`search
--connector` read it directly, offline and keyword-only.

**Fail-closed safety.** `forget` **cannot** delete a `GLOBAL` entry from the CLI
(The World owns `GLOBAL`) — it exits `3`. A hard delete is the default (`forget`
means *gone*); `--archive` keeps the row recoverable. `connect --vault/--path` and
`export --out` resolve every path (symlinks collapsed) and confine it to an allowed
root before any I/O — a `..`/symlink/absolute escape is rejected fail-closed;
`export --out` won't overwrite without `--yes` and writes atomically. A connector
stores only a resolved path reference, never file contents. Guardrail roots and the
export cap are tunable via `ARCANA_MEMORY_SCOPE_PATHS` / `ARCANA_MEMORY_MAX_FILE_MB`.

**Scripting.** Every command (`chat` and `soul edit` aside) accepts `--json` for
machine-readable output — stdout is then exactly one JSON document, the result or
`{"error": {"code", "message"}}` — and all use uniform exit codes: `0` ok, `1`
error, `2` not-found, `3` denied. `--json` never prompts: a destructive op must also
pass `--yes`, and any other question exits `1` with an error document naming the
flag that answers it. The per-command documents are listed in the
[CLI reference](https://docs.arcanaos.cloud/cli/#json-output-json). Piped stdin answers prompts (`echo y | arcana mcp remove x`); if
it runs out before a question is answered, the command exits `1` the same way
instead of hanging. Declining a destructive confirmation (the default is no)
prints `Cancelled.` and exits `1`. API keys and bearer tokens are asked with
hidden input and go only to the OS keyring. Secrets and connector paths never
appear in a shared log or error output.

---

### `arcana run`

Run a prompt against an agent: the one named with `--agent`, or, without it, the one The World routes to.

```bash
arcana run "Summarise the latest on LLM evals" --agent researcher
arcana run "Refactor this module" --agent my-agent --stream
arcana run "Where did we leave off?" --agent researcher --continue
arcana run "One-off, don't remember this" --agent researcher --no-memory
arcana run "Summarise this" --agent researcher --stream | tee summary.md
arcana run "Classify this ticket" --agent triage --json | jq -r .response
```

| Flag | Default | Description                      |
|------|---------|----------------------------------|
| `--agent / -a` | routed by The World | Target agent by name or UUID |
| `--stream / -s` | off | Stream output token by token     |
| `--session` | new session | Resume a specific session by UUID |
| `--continue` | off | Resume the agent's most recent session |
| `--no-memory` | off | Run stateless — don't load or persist memory |
| `--json` | off | Print one JSON document instead; can't be combined with `--stream` |

The agent is rebuilt from its stored record and run through a `ModelGateway` using its configured connection. Each run is recorded to a session under the agent, and — unless `--no-memory` is passed — the agent recalls relevant memory before answering and extracts new memory afterwards through its `MemoryFederation`. The command prints the session id so you can resume it later with `--session` or `--continue`. `--session` and `--continue` are mutually exclusive.

stdout carries the reply alone — the card-bordered panel, or with `--stream` the tokens and a final newline — so it pipes cleanly. The `✦ thinking…` spinner and the notes around the reply (which agent answered, the session id) go to stderr; the spinner only draws when stderr is a terminal and disappears when the first token arrives.

With `--json`, stdout is exactly one document: `{"agent", "session_id", "response"}`, plus `"usage": {"input_tokens", "output_tokens"}` when the model reported token counts. A failure prints `{"error": {"code", "message"}}` instead and exits with that code (`1`). `--json --stream` is a usage error (exit `2`): a token stream has no JSON form.

---

### `arcana chat`

Start an interactive session with a card-configured agent: a scrolling transcript above the chat input and a status line, rendered inline under your shell prompt (full-screen on Windows). It drives the same agent + session + memory path as `arcana run`. Running `arcana` with no command opens the same session; `arcana --help` still lists the commands.

```bash
arcana                                           # The World picks the agent
arcana chat --agent researcher
arcana chat --agent researcher --session <uuid>   # resume a session
arcana chat --agent researcher --no-memory        # stateless session
arcana chat --no-mouse                            # native click-drag selection
```

| Flag | Default | Description |
|------|---------|-------------|
| `--agent / -a` | The World's pick | Target agent by name or UUID; without it The World routes the opening agent, and asks for `--agent` when it can't |
| `--session` | new session | Resume a specific session by UUID |
| `--no-memory` | off | Run stateless — don't load or persist memory |
| `--no-mouse` | off | Turn off mouse capture (also `{"ui": {"mouse": false}}` in `config.json`); bare `arcana --no-mouse` takes it too |

Replies stream into a live block that redraws at most `ARCANA_TUI_STREAM_FPS` times a second (default 30), so partial Markdown re-flows correctly however fast the model is. When the session ends the transcript is printed to your terminal, followed by the `--session <uuid>` resume hint.

Inside the session, slash commands are available (type `/help` to list them, `/<command> --help` for one command's options). The session's own:

| Command | Description |
|---------|-------------|
| `/help` | List every slash command |
| `/memory` | Show what this agent recalls from this session |
| `/card` | Print the resolved card config — temperature, tone, weights |
| `/switch [name]` | Load another agent in a new session; with no name, pick one (previewed by its card) |
| `/retry` | Re-run your last message |
| `/save` | Force a session snapshot to disk now |
| `/clear` | Clear the screen (the session is kept) |
| `/fresh` | Start a new session |
| `/no-memory` | Start a new stateless session (memory off) |
| `/exit` | Close the session and quit |

And every `arcana` command, as `/<group> <action>` with the same options — `/agent create`, `/providers add`, `/mcp approve`, `/memory search`, `/tools subscribe`, `/cards`, `/world route`, `/status` and the rest. They are generated from the command tree, so a slash command is the one-shot command, not a copy. Only `chat`, `run`, `init` and `soul edit` stay outside the session; typing one says why. `Tab` completes command names, actions, options and agent names.

`Enter` sends; `\`+`Enter` and `Ctrl+J` insert a newline on every terminal, and `Alt+Enter` / `Shift+Enter` do on terminals with the kitty keyboard protocol (elsewhere `Alt+Enter` sends: a known Textual limitation, [Textualize/textual#6378](https://github.com/Textualize/textual/issues/6378)). `Ctrl+C` cancels the current turn or command (or quits when idle); `Ctrl+D` quits at an empty prompt. The full keys table, mouse and selection, and terminal support (inline vs. Windows full-screen) are in the [interactive chat docs](https://docs.arcanaos.cloud/chat/). `/clear` clears the screen only: the transcript printed on exit still holds the whole session.

A command asks whatever its options leave out (a required argument included) in dialogs over the session; `Esc` cancels one at any step without saving anything. A secret (`--api-key`, a bearer in `--header`) can't go on the line in the session — it would stay in the transcript and history — so leave it off and answer the hidden prompt. The line is split into words and handed to the command's option parser; it never reaches a shell. An MCP server added in the session stays out of the running agent's tools until `/mcp approve <name> --all`.

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
arcana cards            # browse the Major Arcana in the two-pane picker (needs a terminal)
arcana cards show hermit
```

| Subcommand | Description |
|-----------|-------------|
| *(default)* | Browse the Major Arcana (The World is reserved) in the two-pane picker |
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

The slash-command tables in `docs/chat.md` and `docs/cli.md` are included from `docs/snippets/`, which `tests/test_docs_reference.py` generates from the slash-command registry; the test fails when they are stale. Regenerate them (and any golden transcript) with `ARCANA_RECORD_GOLDENS=1` set, e.g. `ARCANA_RECORD_GOLDENS=1 uv run pytest packages/arcana-cli/tests/test_docs_reference.py`. Release notes are in [CHANGELOG.md](https://github.com/priscilapower/arcana-os/blob/main/packages/arcana-cli/CHANGELOG.md).

### Writing a command against the renderer port

A command body is a coroutine that takes a `Renderer` (`arcana_cli.ui.renderer`) and never touches the terminal directly (a test fails on a `Console(...)`, `print` or `console.print` in command code): `r.emit(...)` for output, `fail(r, message, *details, code=...)` to stop with an error, `r.note(...)` for remarks about the run that aren't its output (stderr on the console and `--json` surfaces), `await r.ask(Question(...))` / `r.confirm(...)` / `r.select([Choice(...)])` for input, `r.status(...)` / `r.stream(...)` for progress, and `async with r.waiting(msg) as wait: wait.show(...)` while the user does something outside the program (OAuth sign-in shows its URL or device code this way: printed lines at a terminal, stderr under `--json`, a dialog in the session whose `Esc` raises `typer.Abort` out of the block). `async with r.status(msg) as status` yields a handle whose `status.stop()` ends the indicator early — `arcana run --stream` stops it on the first token. A result that has a `--json` form is emitted as a `Presentable` — an object with `to_rich()` (what a terminal or the session shows) and `to_json()` (the `--json` document); `View(rich, json)` builds one from both views of the same data, so the command never asks which surface it is on. `fail(...)` hands the renderer a `Failure`: an error line and its details on stderr at a terminal, `{"error": {"code", "message", "details"?}}` on stdout under `--json`, a transcript block in the session. The Typer callback only picks the adapter and runs the coroutine, and `@command_impl` registers the body under the command's Typer path, which is what makes it a slash command in the session too:

```python
@command_impl("cards show")
async def show_card(r: Renderer, name: str) -> None:
    card = _resolve_card(r, name)  # fail(r, f"Unknown card: {name!r}") when there is none
    r.emit(View(card_panel(card, get_registry()), card.model_dump(mode="json")))

@app.command("show")
def show_cmd(name: str, json_: bool = typer.Option(False, "--json", help="Emit JSON")) -> None:
    run_async(show_card(renderer_for(json_), name))
```

`renderer_for(json=...)` returns a `TtyRenderer` (Rich console and line prompts; output on stdout, notes, errors and the status spinner on stderr; a `Verbatim` document — `memory export`'s Markdown — is written exactly as is; a `select` whose choices carry previews opens the two-pane picker as a short-lived Textual app on the command's own event loop, and fails closed with `NonInteractiveError` without a terminal) or a `JsonRenderer` (one `emit_json` document: a `Presentable`'s JSON view, or an `emit_error` document; any question fails closed with `NonInteractiveError`, exit code `1`, as an error document). `arcana_cli/commands/cards.py` is the reference conversion; `arcana_cli/commands/run.py` (`run_turn`) shows status and streaming. In tests, hand the coroutine a `RecordingRenderer` (`tests/support/renderer.py`), which records emitted output, notes and failures (`errors`, `errors_text()`), returns the JSON views of what was emitted (`documents()`), answers questions from a script, and logs statuses, their stops and streamed chunks in order (`events`).

The body's parameters after the renderer are the callback's, by name and type, minus `json_` (and a Typer `Context`), and the callback passes each one straight through — convert or default a value inside the body, never in the callback, because the session calls the body with Typer's parsed values directly. `tests/tui/test_slash_registry.py` holds every command to this, and fails on a new command until it has a body or a reasoned entry in `NOT_IN_SESSION` (`tui/slash_registry.py`). An option whose value can be a secret is declared on the body (`@command_impl("providers add", secrets={"api_key": any_value})`) so the session refuses it on the line and hides it; the same test fails on a new secret-looking option until it is declared, or listed as not a secret with a reason. A parameter that names an agent takes `metavar=AGENT_METAVAR`, so the session completes agent names there.

Every question a command asks goes through the port; a test fails if anything but `TtyRenderer` calls `typer.prompt` / `typer.confirm`. Give each question the `flag` that answers it without a prompt (`Question("Agent name", flag="--name")`, `r.confirm(..., flag="--yes")`), so a surface that can't ask names it in its error; `TtyRenderer` also fails closed that way when piped stdin runs out before an answer. Mark a credential `Question(..., secret=True)`: input is hidden and the answer reaches the caller alone. Put checks in a `validator` (`required` refuses a blank answer) so a bad answer is asked again rather than ending the command; its message is shown to the user, so it must never quote the answer. A destructive command guards the change with `await confirm_or_cancel(r, "Remove …?")`, which defaults to no, notes `Cancelled.` and exits `1` on anything but yes; the command's `--yes` skips it. `agent`, `providers`, `mcp add`/`approve`/`remove`/`login`, `memory forget` and `tools unsubscribe` follow this pattern.

### The interactive app shell

`arcana_cli/tui/` holds `ArcanaApp`, the Textual app the interactive session runs in: a transcript (a `RichLog` that retains every block), a live block for streamed output, the chat input, a status bar, and modal dialogs for questions. `await app.run_inline()` runs it on the caller's event loop — inline under the prompt on macOS/Linux, full-screen on Windows — and, when it exits, prints the retained transcript to stdout so the session lands in terminal scrollback (capped at `ARCANA_TUI_REPLAY_BLOCKS`, default 500). Mouse capture is on unless `config.json` sets `{"ui": {"mouse": false}}`.

Its stylesheet and Textual theme are generated from the colour tokens in `ui/theme.py` (`ACCENT` → `$accent`, `SURFACE_HI` → `$surface-hi`), so `theme.py` stays the only place a colour is defined; a drift test fails if the two disagree.

The chat input (`tui/chat_input.py`) is a `ChatInput` built on Textual's `TextArea`, stacked in a `ChatInputPanel` with its slash-command completion menu and Ctrl+R search bar. It keeps the chat editor's behaviour: Enter submits (posting `ChatInput.Submitted` with collapsed pastes expanded), `\`+Enter and Ctrl+J insert a newline, and so do Alt+Enter and Shift+Enter on terminals with the kitty keyboard protocol (elsewhere the terminal sends Alt+Enter as a plain Enter, which submits); pastes of four lines or more collapse to `[pasted N lines]`; a leading `/` completes command names, a group's actions, a command's options and agent names wherever an argument names an agent; Up/Down walk the agent's history, with a ghost suggestion from it and Ctrl+R reverse search. Ctrl+Z undoes, a paste included. History (`tui/history.py`) stays in prompt_toolkit's `FileHistory` format at `~/.arcana/agents/<uuid>/chat_history`, so existing history files carry over (prompt_toolkit is no longer a dependency: a file it wrote, recorded under `tests/tui/fixtures/`, pins the format byte for byte); a history file that can't be read or written falls back to in-memory history. Completion (`tui/completion.py`) is a pure function.

`TextualRenderer` (`arcana_cli.ui.renderer.textual_renderer`) is the renderer-port adapter over a running app. Its question methods must be awaited from an app worker (`app.run_worker(...)`); Esc cancels any dialog, and a secret answer never reaches the transcript or the exit replay. It is imported from its module rather than `arcana_cli.ui.renderer`, so the one-shot and `--json` paths never load Textual.

The chat session (`commands/chat/`) runs on it: `ChatApp` adds the session's keys (priority Ctrl+C / Ctrl+D bindings, so they beat the input box's copy and delete-forward), and `_ChatController` holds the session logic and writes only through the renderer port, so its tests hand it a `RecordingRenderer`. A turn runs as an app worker, so anything it awaits, such as a `ToolConfirmer` passed through `build_session_runtime(..., confirmer=...)`, can push a dialog and wait for the answer; cancelling the turn takes the dialog down with it. `Renderer.stream(prefix, render=...)` takes a function from the text so far to the block shown, which is how a reply re-renders as Markdown while it streams. `commands/chat/command.py` settles the agent and session before loading the app module, so `arcana_cli.main` still imports without Textual. The slash commands come from `tui/slash_registry.py`: `build_registry()` walks the Click tree `typer.main.get_command(main.app)` builds, joins each leaf to the body registered for its path (`arcana_cli/command_impl.py`), and gives each a `SlashCommand` that parses a line with the leaf's own Click parameters (`make_context`, no help option, a missing required argument asked for through the renderer), converts the values with Typer's own convertors, hides surface-only (`--json`) and secret options, and awaits the body. `SlashRegistry.redacted()` spots a line carrying a secret, so it is refused and kept out of the transcript and history; `SlashRegistry.vocabulary()` is what the input box completes against (`tui/completion.py`). The session's own commands (`SESSION_COMMANDS` in `ui/input_model.py`) win over generated ones. The controller runs a command inside the turn's worker, turns `typer.Exit` / `typer.Abort` / any exception into a note, and then refreshes what the command's group touched: the agent (`agent`, `tools`: rebuilding the runtime when the current one changed), the model connections (`ConnectionStore.reload()` and the gateway's cached adapters dropped), or the session's MCP tool registry (`MCPRegistry.without(...)` keeps servers added in the session out until `/mcp approve`).

The two-pane picker (`tui/card_picker.py`) is `CardPickerScreen`, a modal screen with a filter box, the list, and a scrollable preview of the highlighted choice's `Choice.preview`; single pick or `Space` multi-select with `max_items`, `Esc`/`Ctrl+C` to cancel. Any `Renderer.select` whose choices carry previews opens it: `TextualRenderer` pushes it over the session (a bare `/switch` uses it to pick an agent), `TtyRenderer` runs it in `PickerApp` (`await pick(...)`). `ui/card_picker.py` keeps `select_card` / `select_cards` as async wrappers over `Renderer.select` with `card_choices()`, which leaves THE WORLD out unless the caller's `exclude` lets it in. No module reads raw keys any other way, and none runs a `rich.live` display: Textual is the only terminal toolkit. `tests/test_terminal_drivers.py` bans `readchar`, `prompt_toolkit` and `rich.live` imports, and keeps `readchar` and `prompt_toolkit` out of the package's dependencies, direct or transitive (`uv.lock`).

UI tests drive the app headless through Textual's Pilot: `tests/support/tui.py` provides `arcana_pilot()` (exposed as the `tui` fixture under `tests/tui/`), which yields the app, its pilot, and a bound `TextualRenderer`.

---

## Roadmap

Agents run with persistent sessions and the federated memory layer wired into `run` and `chat`. **The World** now resolves *which agent runs a task*: `arcana world route "<prompt>"` shows the decision as a dry run, and `run`/`chat` route their first turn through it when you don't pass `--agent`. Explicit / rule / default resolution is deterministic; set `ARCANA_REFLEX_MODEL` to a `provider/model_id` reference to enable the **reflex classifier** — a cheap local model picks the best agent for tasks no rule covers, and `run` surfaces a *"couldn't confidently route"* notice on a low-confidence pick. Still to come are the commands whose backends land later — `arcana spread`, and the rest of the World meta-agent (briefings and cross-agent memory).
