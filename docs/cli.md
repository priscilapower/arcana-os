# CLI reference

The `arcana` command is a thin [Typer](https://typer.tiangolo.com/) wrapper
around `arcana-core`.

!!! info "Auto-generated"
    Everything below is generated from the live CLI at build time, so it always
    matches the released version — commands, subcommands, flags, and help text
    can't drift from the code. New `arcana` commands appear here automatically on
    the next docs deploy. For install and first-run steps, see
    [Getting started](getting-started.md).

::: mkdocs-typer2
    :module: arcana_cli.main
    :name: arcana
    :pretty: true

## JSON output (`--json`)

Every command except the interactive ones (`arcana chat`, `arcana soul edit`)
takes `--json`, and they all keep the same contract, so a script can drive any
of them the same way:

- **stdout is exactly one JSON document**: the command's result, or an error
  document. Nothing else is written to stdout: remarks (which agent The World
  routed to, warnings, OAuth sign-in instructions) go to stderr.
- **Errors** print `{"error": {"code": <exit code>, "message": "..."}}`, plus
  `"details": [...]` (plain-text hint lines, or the candidate IDs of an
  ambiguous name) when there are any, and the command exits with `code`.
- **Exit codes** are uniform: `0` ok, `1` error, `2` not found, `3` denied (a
  changed MCP tool awaiting approval, a `GLOBAL` memory entry). A command-line
  usage error (an unknown option, a missing argument, `--json` with `--stream`)
  is reported by the argument parser on stderr and exits `2`.
- **`--json` never prompts.** A question the flags didn't answer fails closed
  with an error document naming the flag that answers it (for example
  `pass --yes instead`), exit `1`.
- **Shapes**: lists are arrays of objects, IDs are strings, timestamps are
  ISO 8601 strings, and no document contains ANSI escapes or a secret (API keys
  and tokens live only in the OS keyring; a document carries at most a keyring
  reference or a redacted summary, the same one the human view shows).

```bash
arcana agent list --json | jq -r '.[].name'
arcana mcp show notion --json | jq '.tools | length'
arcana providers remove work --yes --json || echo "exit $?"
```

### Documents per command

| Command | Document |
|---|---|
| `arcana init` | `{home, created}` (`created: false` when `~/.arcana` already exists) |
| `arcana status` | `{home, agents, connections}` (counts) |
| `arcana run` | `{agent, session_id, response}`, plus `usage: {input_tokens, output_tokens}` when reported |
| `arcana agent list` | `[{id, name, card, model, status}]` |
| `arcana agent show` / `create` / `edit` | `{id, name, card, model, status, description, modifier_cards, temperature, tags, tool_subscriptions, created_at, system_prompt}` |
| `arcana agent delete` | `{deleted, id}` |
| `arcana providers list` | `[{id, name, provider, default_model, endpoint, auth_type}]` |
| `arcana providers show` | the list object plus `{headers, credential, created_at, updated_at}`; `credential` is the redacted summary (e.g. `API key in keyring (<ref>)`) |
| `arcana providers add` | the list object plus `{action}` (`added` / `updated`) |
| `arcana providers edit` | the list object plus `{health}` (`healthy` / `down` / `unknown`, `null` when not checked) |
| `arcana providers login` | the list object plus `{signed_in: true}` |
| `arcana providers remove` | `{removed}`; with dependent agents and no `--force`, exit `1` and `{aborted: "dependents", dependents: [{agent, id, model}]}` |
| `arcana cards --json` | the catalog (no picker): `[{id, number, name, role}]`, every card but The World |
| `arcana cards show` | the full card definition (`id`, `name`, `number`, `archetype`, `synergy_cards`, …) |
| `arcana soul show` | `{path, exists, content}` (`content: null` when there is no `soul.md`) |
| `arcana mcp list` | `[{name, transport, auth_type, status, tools, changed_tools}]` |
| `arcana mcp show` / `add` / `login` | `{name, transport, server_url, command, args, status, description, auth_type, auth_key_ref, token_expires_at, tools: [{name, qualified_name, status, description}]}`; after `add` / `login` / `refresh`, a server that couldn't be reached still prints its document and exits `1` |
| `arcana mcp refresh` | the `show` document plus `{newly_changed}` |
| `arcana mcp approve` | `{approved, status}` |
| `arcana mcp remove` | `{removed}`; with subscribed agents and no `--force`, exit `1` and `{aborted: "dependents", dependents: [{agent, tools}]}` |
| `arcana memory list` / `search` | `[{id, type, importance, confidence, scope, pool, pinned, content, created}]` |
| `arcana memory inspect` | the entry plus `{confidence_source, last_accessed, access_count, decay_factor, effective_importance, source_session_id, has_conflict, archived}` |
| `arcana memory forget` | `{id, forgotten, hard, scope, pool}` |
| `arcana memory connect obsidian` / `markdown` | `{name, kind, path}` |
| `arcana memory adapters` | `[{name, kind, path, healthy, notes}]` |
| `arcana memory export` | `{exported, bytes}`, plus `out` with `--out` (without `--json` the Markdown itself goes to stdout) |
| `arcana tools list` | `[{qualified_name, type, server, description}]`, plus `subscribed` with `--agent` |
| `arcana tools subscribe` | `{agent, tool, subscribed, changed}`, plus `{covered_tools, tool_subscriptions}` when it changed |
| `arcana tools unsubscribe` | `{agent, tool, subscribed: false, tool_subscriptions}` |
| `arcana world route` | the routing decision (`layer`, `resolved_agent_id`, `candidate_pool`, …); when The World can't pick an agent, the same document with exit `1` |
