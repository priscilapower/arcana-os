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
the session runs full-screen rather than inline; the transcript is printed on
exit all the same.

Replies stream in and render as Markdown, re-rendered as they grow so lists and
code blocks settle into shape mid-reply. Model reasoning wrapped in
`<think>…</think>` is dimmed rather than shown as raw tags, and inline LaTeX
(`$…$`, `\(…\)`) is converted to Unicode where it can be.

## Slash commands

Type `/` in the input to open the command menu; `Tab` completes and cycles it.

The session has a few commands of its own:

| Command | What it does |
| --- | --- |
| `/help` | list every slash command |
| `/memory` | show what this agent recalls from this session |
| `/card` | print the resolved card config — temperature, tone, weights |
| `/switch [name]` | load another agent in a new session (no name: pick one) |
| `/retry` | re-run your last message |
| `/save` | force a session snapshot to disk now |
| `/clear` | clear the screen (the session is kept, and printed in full on exit) |
| `/fresh` | start a new session |
| `/no-memory` | start a new stateless session (memory off) |
| `/exit` | close the session and quit |

And every `arcana` command is a slash command too — the same command, not a
copy of it. `arcana <group> <action> …` becomes `/<group> <action> …`:

| Command | What it does |
| --- | --- |
| `/agent create\|list\|show\|edit\|delete` | manage agents |
| `/cards`, `/cards show <card>` | browse the Major Arcana, or show one card |
| `/providers list\|add\|login\|show\|edit\|remove` | manage model provider connections |
| `/mcp add\|list\|show\|refresh\|login\|approve\|remove` | manage MCP servers; a server's tools reach this session once approved |
| `/tools list\|subscribe\|unsubscribe` | inspect tools and an agent's subscriptions |
| `/memory list\|search\|inspect\|forget\|adapters\|export\|connect` | inspect, forget, connect and export memory (`/memory` alone is this session's recall, above) |
| `/soul show` | print your `soul.md` |
| `/world route <prompt>` | which agent The World would pick, without running it |
| `/status` | home directory, agents and connections |

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
| `Enter` | send the message |
| `\+Enter`, `Ctrl+J` | insert a newline (every terminal) |
| `Shift+Enter`, `Alt+Enter` | insert a newline on terminals with the kitty keyboard protocol (Ghostty, kitty, WezTerm, iTerm2 with CSI u); elsewhere they send the message |
| `↑` / `↓` | recall input history (kept per agent) |
| `Ctrl+R` | reverse-search history |
| `Tab` | open / cycle the slash-command menu |
| `Ctrl+C` | cancel a streaming reply, or quit when idle |
| `Ctrl+D` | quit at an empty prompt |
| `Ctrl+L` | repaint the screen |

The mouse is captured so the wheel scrolls the transcript and dialogs are
clickable; hold `Shift` (`Option` in iTerm2) to select text natively. Start with
`--no-mouse` (or set `{"ui": {"mouse": false}}` in `~/.arcana/config.json`) to
turn capture off and select without a modifier, at the cost of wheel scrolling.

Pasting a large block collapses it to a `[pasted N lines]` placeholder in the
input; the full text is restored when you send the message, so a long paste
doesn't flood the editor.

## Memory

Like `arcana run`, chat gives the agent its private memory by default, so it
recalls earlier sessions. `/memory` shows what's recalled for the current
conversation. Start stateless with `--no-memory` (or `/no-memory` mid-session);
`/fresh` opens a new session with memory back on. See
[Memory → Assembling a federation](api/memory.md#assembling-a-federation-for-an-agent)
for how memory is wired.
