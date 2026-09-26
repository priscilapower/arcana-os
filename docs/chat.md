# Interactive chat

`arcana chat` opens a persistent session with a card-configured agent: a
scrolling transcript above the chat input and a status line, drawn inline under
your shell prompt. It runs the same agent + session + memory path as
[`arcana run`](cli.md), just interactively instead of one shot.

## Start a session

```bash
arcana                          # The World picks the agent
arcana chat --agent researcher
```

Plain `arcana`, with no command, opens the same session as `arcana chat`
(`arcana --help` still lists every command).

Pass an agent by name or UUID with `--agent` / `-a`. Omit it and **The World**
picks the agent for you — its configured default, or the sole agent if you only
have one; when it can't decide it asks you to name one with `--agent`. Add
`--no-memory` to run stateless, or `--session <uuid>` to resume an earlier
session — its recent turns are replayed on screen so you pick up where you left
off. The full flag list is in the [CLI reference](cli.md).

When the session ends, its transcript is printed to your terminal so it stays in
scrollback, followed by the `--session <uuid>` line that resumes it. On Windows
the session runs full-screen rather than inline (see
[Terminal support](#terminal-support)).

Replies stream in and render as Markdown, re-rendered as they grow so lists and
code blocks settle into shape mid-reply. Model reasoning wrapped in
`<think>…</think>` is dimmed rather than shown as raw tags, and inline LaTeX
(`$…$`, `\(…\)`) is converted to Unicode where it can be.

## Slash commands

Type `/` in the input to open the command menu; `Tab` completes and cycles it.

The session has a few commands of its own:

--8<-- "docs/snippets/slash-session-commands.md"

And every `arcana` command is a slash command too — the same command, not a
copy of it. `arcana <group> <action> …` becomes `/<group> <action> …`:

--8<-- "docs/snippets/slash-command-groups.md"

Both tables are generated from the session's own command registry, so they
can't fall behind it; the [CLI reference](cli.md#commands-and-the-session)
maps every `arcana` command to its slash command.

`/help` lists them all, and `/<command> --help` lists the options a command
takes in the session. A few commands stay outside it: `arcana chat` (you are
in it), `arcana run` (type the prompt instead, or `/switch` first), `arcana
init` (a session only opens on an initialised home) and `arcana soul edit`
(it hands the terminal to `$EDITOR`); typing one says so.

`Tab` completes a command's name, then its action (`/agent e` → `edit`), then
its options after a `-` (`/agent edit --c` → `--card`), and agent names where
an argument names an agent: `/switch <name>`, `/agent edit <name>`,
`--agent <name>`. A bare `/switch` opens the agent picker: every agent beside a
preview of its primary card. Type to filter, `↑` / `↓` to move, `Enter` to
switch, `Esc` to stay where you are.

## Running commands without leaving the session

A slash command takes the same options as its `arcana` command, parsed the same
way: `/providers add --provider ollama` means what `arcana providers add
--provider ollama` means, and does exactly the same thing. Whatever the options
leave out is asked in a dialog over the session — a required argument too
(`/agent edit` asks which agent):

```text
/providers add                                  # every answer in dialogs
/agent create --name researcher                 # then pick its card
/mcp add --name notion-mcp --url https://mcp.notion.com/mcp
/providers login work --device                  # OAuth sign-in again
/memory search "release notes" --agent scout
/tools subscribe scout notion-mcp
```

- **`Esc` cancels the command** at any step and nothing is saved — no
  half-written connection and no key left in the keyring. On a yes/no question
  `Esc` answers no. An empty `Enter` takes a question's default. `Ctrl+C`
  cancels the command too.
- **Secrets are asked, never typed on the line.** `--api-key`, or a bearer token
  in `--header`, is refused inside the session (on any command, even a
  mistyped one): the line would stay in the transcript and your input history.
  Leave it off and the command asks for it in a hidden prompt; the answer goes
  to the OS keyring and shows as `(hidden)`. Such options don't appear in
  completion or `--help` either.
- **The line is never a shell command.** It is split into words the way a shell
  would split it (so quote a value with spaces), and the words go to the
  command's own option parser — nothing reaches a shell, so `;`, `|` or `$(…)`
  are just characters.
- **OAuth sign-in** opens a dialog: the browser flow shows the URL to open if
  your browser doesn't, the device flow (`--device`) shows the code to enter.
  Select it with `Shift`-drag (`Option`-drag in iTerm2) to copy it. `Esc` calls
  the sign-in off and closes the local callback listener.
- **An MCP server added in the session isn't in the current agent's tools until
  you approve it** with `/mcp approve <name> --all` — so nothing said during
  the conversation can widen what the agent can do. `/mcp remove` takes a
  server's tools away at once. Slash commands only ever come from what you
  type: a reply that says `/mcp approve …` is just text.
- After a command the session picks up what it changed: an agent you create can
  be `/switch`ed to straight away, editing the current agent (or its tool
  subscriptions) reloads it, and a provider change applies from the next reply.
  `--json` isn't available inside the session.
- A command that fails, or whose options don't parse, leaves a note in the
  transcript; the session carries on.

While the session runs, a stdio MCP server's error output goes to
`~/.arcana/logs/mcp-stdio.log` instead of the terminal.

## Keys

| Key | Action |
| --- | --- |
| `Enter` | send the message (in a dialog: answer; on the picker: choose) |
| `\` then `Enter`, `Ctrl+J` | insert a newline (every terminal) |
| `Shift+Enter`, `Alt+Enter` | insert a newline on terminals with the kitty keyboard protocol; elsewhere they send the message (see [below](#newlines-and-alt-enter)) |
| `↑` / `↓` | recall input history (kept per agent) |
| `→` | accept the greyed-out suggestion from history |
| `Ctrl+R` | reverse-search history (`Ctrl+R` again: an older match; `Enter` takes it without sending) |
| `Tab` / `Shift+Tab` | open / cycle the slash-command menu |
| `Esc` | close the menu or the search and restore what you typed; cancel a dialog or the picker |
| `Ctrl+Z` | undo (a whole paste in one step) |
| `Ctrl+C` | cancel a streaming reply or a running command, or quit when idle |
| `Ctrl+D` | quit at an empty prompt |
| `Ctrl+L` | repaint the screen |

### Newlines and Alt+Enter {#newlines-and-alt-enter}

`\`+`Enter` and `Ctrl+J` insert a newline on every terminal: use them if you
aren't sure what yours sends.

`Shift+Enter` and `Alt+Enter` (`Option+Enter` on a Mac) need a terminal that
speaks the [kitty keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/),
which the session turns on when the terminal supports it: Ghostty, kitty,
WezTerm, and iTerm2 with *Report keys using CSI u* on. Other terminals —
macOS Terminal.app, iTerm2 with CSI u off, tmux without `extended-keys`, most
VTE-based terminals — send `Alt+Enter` as `Esc` followed by `Enter`, which the
terminal library the session is built on ([Textual](https://textual.textualize.io/))
reads as a plain `Enter`, so the message is **sent** rather than broken. This
is a known limitation, tracked upstream in
[Textualize/textual#6378](https://github.com/Textualize/textual/issues/6378);
once a Textual release fixes it, `Alt+Enter` will insert a newline everywhere.

Pasting a large block collapses it to a `[pasted N lines]` placeholder in the
input; the full text is restored when you send the message, so a long paste
doesn't flood the editor.

## Mouse, selection and scrollback

The mouse is captured so the wheel scrolls the transcript and dialogs are
clickable; hold `Shift` (`Option` in iTerm2) while dragging to select text
natively. Start with `--no-mouse` (it works on bare `arcana` too), or set
`{"ui": {"mouse": false}}` in `~/.arcana/config.json`, to turn capture off and
select without a modifier, at the cost of wheel scrolling and clicking.

While the session runs, its transcript scrolls inside the session rather than
into your terminal's scrollback. When it ends, the whole transcript is printed
to the terminal, so it lands in scrollback where you can scroll, search and
copy it, followed by the `--session <uuid>` line that resumes it.

## Terminal support

| | macOS, Linux | Windows |
| --- | --- | --- |
| Where the session draws | inline, under your shell prompt | full-screen (Textual has no inline mode on Windows) |
| Transcript printed on exit | yes | yes |
| Mouse capture, `--no-mouse` | yes | yes |
| `Shift+Enter` / `Alt+Enter` newline | kitty-protocol terminals only | not yet verified |
| `\`+`Enter` newline | yes | yes |
| `Ctrl+J` newline | yes | not yet verified |

The session needs a terminal. In scripts and CI use the one-shot commands
(`arcana run`, `arcana agent list …`) with `--json`: they never prompt, and a
question the flags didn't answer fails with an error naming the flag that
answers it (see [JSON output](cli.md#json-output-json)).

## Memory

Like `arcana run`, chat gives the agent its private memory by default, so it
recalls earlier sessions. `/memory` shows what's recalled for the current
conversation. Start stateless with `--no-memory` (or `/no-memory` mid-session);
`/fresh` opens a new session with memory back on. See
[Memory → Assembling a federation](api/memory.md#assembling-a-federation-for-an-agent)
for how memory is wired.
