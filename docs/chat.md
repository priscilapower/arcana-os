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

| Command | What it does |
| --- | --- |
| `/help` | list these commands |
| `/memory` | show what this agent recalls from this session |
| `/card` | print the resolved card config — temperature, tone, weights |
| `/switch [name]` | load another agent in a new session (no name: pick one) |
| `/retry` | re-run your last message |
| `/save` | force a session snapshot to disk now |
| `/clear` | clear the screen (the session is kept, and printed in full on exit) |
| `/fresh` | start a new session |
| `/no-memory` | start a new stateless session (memory off) |
| `/exit` | close the session and quit |

After `/switch`, `Tab` also completes agent names. A bare `/switch` opens the
agent picker: every agent beside a preview of its primary card. Type to filter,
`↑` / `↓` to move, `Enter` to switch, `Esc` to stay where you are.

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
