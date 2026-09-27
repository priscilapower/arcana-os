# Changelog — arcana-cli

Notable changes to the `arcana-cli` package. Each package in the monorepo is
versioned and released on its own (tag `arcana-cli-vX.Y.Z`).

## 0.4.0 — unreleased

The CLI now drives the terminal through one toolkit, [Textual](https://textual.textualize.io/),
and the interactive session is the front door
([ADR-024](https://app.notion.com/p/3e1c2f931b17819c9f28dcaec6cc264b)).

### Changed — read before upgrading

- **Bare `arcana` opens the chat session** (The World picks the agent), as
  `arcana chat` does. It used to print the help page: use `arcana --help` for
  that now. Scripts that ran bare `arcana` to list the commands should call
  `arcana --help`.
- **The session is a Textual app, drawn inline under your prompt** on macOS
  and Linux and **full-screen on Windows**. When it ends, the transcript is
  printed to the terminal so it lands in scrollback.
- **The mouse is captured in the session** (wheel scrolling, clickable
  dialogs): hold `Shift` (`Option` in iTerm2) to select text natively, or
  start with `--no-mouse` / set `{"ui": {"mouse": false}}` in
  `~/.arcana/config.json`.
- **Questions fail closed when they can't be answered.** When piped input runs
  out before a question is answered, or a picker has no terminal, the command
  exits `1` with an error naming the flag that answers it (it used to print
  `Aborted!`); `--json` never prompts. Piped input still answers line prompts.
- `arcana run` writes only the reply to stdout; the spinner and notes (routing,
  agent, session footer) go to stderr.

### Added

- Every `arcana <group> <action>` command runs inside the session as
  `/<group> <action>`, with the same options, generated from the command tree:
  set up providers, MCP servers and agents without leaving the chat. A secret
  (`--api-key`, a bearer token) is refused on the line and asked in a hidden
  prompt instead.
- `--json` on every command but `chat` and `soul edit`, with one contract (one
  document on stdout, `{"error": {...}}` on failure, uniform exit codes); see
  the [CLI reference](https://docs.arcanaos.cloud/cli/#json-output-json).
- The two-pane card picker opens inside the session too: a bare `/switch`
  picks an agent beside a preview of its card.
- `--no-mouse` on `arcana` and `arcana chat`; `--yes` on `providers add`.
- In the chat input: `Ctrl+Z` undo (a whole paste in one step) and
  `Shift+Enter` newline on kitty-protocol terminals.

### Removed

- The `prompt_toolkit` and `readchar` dependencies. Existing per-agent
  `chat_history` files keep working: they are read and written in the same
  format.
