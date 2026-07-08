# Interactive chat

`arcana chat` opens a persistent, full-screen REPL with a card-configured agent:
a scrolling transcript sits above a pinned input box and footer, so the prompt
never drifts up the screen. It runs the same agent + session + memory path as
[`arcana run`](cli.md), just interactively instead of one shot.

## Start a session

```bash
arcana chat --agent researcher
```

Pass an agent by name or UUID with `--agent` / `-a`. Add `--no-memory` to run
stateless, or `--session <uuid>` to resume an earlier session — its recent turns
are replayed on screen so you pick up where you left off. The full flag list is
in the [CLI reference](cli.md).

Replies stream in and render as Markdown. Model reasoning wrapped in
`<think>…</think>` is dimmed rather than shown as raw tags, and inline LaTeX
(`$…$`, `\(…\)`) is converted to Unicode where it can be.

## Slash commands

Type `/` in the input to open the command menu; `Tab` completes and cycles it.

| Command | What it does |
| --- | --- |
| `/help` | list these commands |
| `/memory` | show what this agent recalls from this session |
| `/card` | print the resolved card config — temperature, tone, weights |
| `/switch <name>` | load another agent in a new session |
| `/retry` | re-run your last message |
| `/save` | force a session snapshot to disk now |
| `/clear` | clear the transcript (history is kept) |
| `/fresh` | start a new session |
| `/no-memory` | start a new stateless session (memory off) |
| `/exit` | close the session and quit |

After `/switch`, `Tab` also completes agent names.

## Keys

| Key | Action |
| --- | --- |
| `Enter` | send the message |
| `\+Enter`, `Shift+Enter`, `Alt+Enter`, `Ctrl+J` | insert a newline |
| `↑` / `↓` | recall input history (kept per agent) |
| `Ctrl+R` | reverse-search history |
| `Tab` | open / cycle the slash-command menu |
| `Ctrl+C` | cancel a streaming reply, or quit when idle |
| `Ctrl+D` | quit at an empty prompt |
| `Ctrl+L` | repaint the screen |

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
