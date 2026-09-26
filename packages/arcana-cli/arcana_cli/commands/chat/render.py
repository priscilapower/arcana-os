"""Transcript blocks for the chat session.

Rich owns every visual (Markdown replies, panels, tables); this module turns
model output and session state into Rich renderables the session writes to its
transcript. It covers splitting a reply into dimmed reasoning + answer, the block
builders (header, user bubble, tables, resume replay) and the status-bar line.
"""

import re
from uuid import UUID

from rich import box
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from arcana.agents.agent import Agent as RuntimeAgent
from arcana.memory.federation import MemoryFederation
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana.types.memory import MemoryQuery, RetrievalMode
from arcana.types.session import MessageRole, Session
from arcana_cli.tui.slash_registry import SlashCommand, SlashRegistry
from arcana_cli.ui.input_model import SESSION_COMMANDS
from arcana_cli.ui.mathtext import normalize_math
from arcana_cli.ui.theme import (
    ACCENT,
    AMBER,
    GREEN,
    PROMPT,
    SEP,
    SURFACE,
    SURFACE_ACCENT,
    TXT,
    TXT2,
    TXT3,
    card_color,
    dim,
    err,
    hl,
    make_table,
)

# Package-internal exports — consumed by the controller and the tests. Declared
# so the split (these helpers live one import away from their callers) doesn't
# read as dead code under strict unused-symbol checks.
__all__ = [
    "_agent_eyebrow_block",
    "_card_table",
    "_command_help",
    "_footer_line",
    "_header_block",
    "_help_table",
    "_live_reply",
    "_memory_renderable",
    "_note_block",
    "_render_reply",
    "_replay_blocks",
    "_split_reasoning",
    "_user_block",
]

# Reasoning-model output: <think>…</think> (or <thinking>…</thinking>) blocks.
_THINK_BLOCK_RE = re.compile(r"<think(?:ing)?>(.*?)</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think(?:ing)?>(.*)\Z", re.DOTALL | re.IGNORECASE)


def _split_reasoning(text: str) -> list[tuple[str, str]]:
    """Split reply text into ordered ("think" | "answer", segment) parts.

    Closed ``<think>…</think>`` blocks become "think" segments; everything else is
    "answer". A trailing unclosed ``<think>`` (mid-stream) is treated as in-progress
    reasoning so it renders dimmed the moment it starts, not as a raw tag.
    """
    segments: list[tuple[str, str]] = []
    pos = 0
    for m in _THINK_BLOCK_RE.finditer(text):
        if m.start() > pos:
            segments.append(("answer", text[pos : m.start()]))
        segments.append(("think", m.group(1)))
        pos = m.end()
    rest = text[pos:]
    open_m = _THINK_OPEN_RE.search(rest)
    if open_m:
        if open_m.start() > 0:
            segments.append(("answer", rest[: open_m.start()]))
        segments.append(("think", open_m.group(1)))
    elif rest:
        segments.append(("answer", rest))
    return segments


def _render_reply(text: str) -> RenderableType:
    """Render a reply: reasoning dimmed, the answer as Markdown, in stream order."""
    text = normalize_math(text)
    renderables: list[RenderableType] = []
    for kind, seg in _split_reasoning(text):
        if kind == "think":
            body = seg.strip()
            if body:
                renderables.append(Text(body, style=f"italic {TXT3}"))
        elif seg.strip():
            renderables.append(Markdown(seg))
    return Group(*renderables) if renderables else Text("")


#: What a reply shows until its first token arrives.
_THINKING = Text("…thinking", style=f"italic {TXT3}")


def _live_reply(text: str) -> RenderableType:
    """A reply as it streams: the ``…thinking`` placeholder until text arrives, then :func:`_render_reply`."""
    return _render_reply(text) if text else _THINKING


# ---------------------------------------------------------------------------
# Transcript block builders
# ---------------------------------------------------------------------------
def _card_title(card: Card) -> str:
    """Human title for a card slug, e.g. ``the-high-priestess`` → ``The High Priestess``."""
    return card.value.replace("-", " ").title()


def _header_block(record: AgentRecord, *, memory_off: bool) -> RenderableType:
    """The session header: agent + card, model, memory state, and a newline tip."""
    accent = card_color(record.card)
    backslash_enter = hl("\\+Enter")
    newline_keys = f"{backslash_enter} or {hl('Ctrl+J')}"
    memory_bit = f"[{TXT3}]off[/]" if memory_off else f"[{GREEN}]on[/]"
    body = Group(
        # Wordmark — cyan glyph + amber wordmark (the canonical brand pairing).
        Text.from_markup(f"[bold {ACCENT}]{PROMPT}[/] [bold {AMBER}]ARCANA[/]"),
        Text(""),
        Text.from_markup(f"[bold {accent}]{escape(record.name)}[/]  [{TXT3}]{escape(_card_title(record.card))}[/]"),
        Text.from_markup(f"[{TXT3}]{escape(record.model)}[/]"),
        Text(""),
        Text.from_markup(f"[{TXT2}]{newline_keys} for a newline[/]"),
        Text.from_markup(f"[{TXT2}]Memory {memory_bit}[/]"),
    )
    return Panel(body, box=box.ROUNDED, style=f"on {SURFACE}", border_style=TXT3, padding=(1, 2), expand=False)


def _user_block(text: str) -> RenderableType:
    """A submitted user message: a ``✦ YOU`` label + a bubble.

    ``Text`` (not markup) carries the content, so it needs no escaping and can't
    inject styles; the bubble fills to its content so short messages stay compact.
    """
    bubble = Panel(
        Text(text, style=TXT),
        box=box.ROUNDED,
        style=f"on {SURFACE_ACCENT}",
        border_style=SURFACE_ACCENT,
        padding=(0, 1),
        expand=False,
    )
    return Group(Text(""), Text.from_markup(f"[bold {ACCENT}]{PROMPT} YOU[/]"), bubble)


def _agent_eyebrow_block(name: str, accent: str) -> RenderableType:
    """The ``✦ NAME`` label above an assistant reply, in the agent's card colour."""
    return Group(
        Text(""),
        Text.from_markup(f"[bold {accent}]{PROMPT} {escape(name.upper())}[/]"),
        Text(""),
    )


def _note_block(markup: str) -> RenderableType:
    """A short standalone status line (dim note or error) with a leading gap."""
    return Group(Text(""), Text.from_markup(markup))


#: How ``/help`` points at a command's own options.
HELP_HINT = "/<command> --help lists a command's options; each runs as arcana <command> does."


def _help_table(registry: SlashRegistry) -> RenderableType:
    """Every slash command: the session's own, then each ``arcana`` command group and top-level command."""
    table = make_table("In-session commands")
    table.add_column("", style=f"bold {ACCENT}", no_wrap=True)
    table.add_column("")
    for command in SESSION_COMMANDS:
        table.add_row(Text(command.usage), Text(command.help))
    table.add_section()
    for group, actions in registry.groups.items():
        if " " not in group and actions:  # a nested group shows as one of its parent's actions
            table.add_row(
                Text(group), Text.assemble((" · ".join(actions), "bold"), "\n", (registry.group_summary(group), TXT3))
            )
    for command in registry.commands.values():
        if len(command.path) == 1 and command.name not in registry.groups:
            table.add_row(Text(command.usage()), Text(command.summary))
    return Group(table, Text(HELP_HINT, style=TXT3))


def _command_help(command: SlashCommand) -> RenderableType:
    """One slash command's help: what it does, its usage and the options it takes in the session."""
    table = make_table(escape(command.usage()))
    table.add_column("", style=f"bold {ACCENT}", no_wrap=True)
    table.add_column("")
    for param in command.visible_params():
        name = ", ".join(param.opts) if param.param_type_name == "option" else param.human_readable_name
        table.add_row(Text(name), Text(param.help or ""))
    return Group(Text(command.summary), table)


def _card_table(runtime_agent: RuntimeAgent, record: AgentRecord) -> RenderableType:
    """The resolved card configuration for the active agent."""
    cfg = runtime_agent.card_config
    table = make_table(f"Card — {record.card.value}")
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Temperature", f"{cfg.temperature:.2f}")
    if record.modifier_cards:
        table.add_row("Modifiers", ", ".join(c.value for c in record.modifier_cards))
    weights = cfg.memory_weights
    table.add_row(
        "Memory weights",
        f"episodic {weights.episodic:.2f} · semantic {weights.semantic:.2f} · "
        f"procedural {weights.procedural:.2f} · preference {weights.preference:.2f}",
    )
    if cfg.suggested_skill_ids:
        table.add_row("Suggested skills", ", ".join(cfg.suggested_skill_ids))
    return table


async def _memory_renderable(federation: MemoryFederation | None, session: Session) -> RenderableType:
    """What this agent recalls, keyed off the session's recent user turns."""
    if federation is None:
        return Text.from_markup(dim("Memory is off for this session (--no-memory)."))

    user_turns = [m.content for m in session.messages if m.role == MessageRole.USER]
    if not user_turns:
        return Text.from_markup(dim("No memories recalled yet, say something first."))

    query = MemoryQuery(text="\n".join(user_turns[-3:]), retrieval_mode=RetrievalMode.keyword, limit=10)
    try:
        entries = await federation.search(query)
    except Exception as exc:
        return Text.from_markup(err(f"Memory unavailable: {escape(str(exc))}"))

    if not entries:
        return Text.from_markup(dim("Nothing recalled for this conversation yet."))

    table = make_table("Recalled memory")
    table.add_column("type", style=TXT3)
    table.add_column("content")
    table.add_column("importance", style=TXT3, justify="right")
    for e in entries:
        table.add_row(e.type.value, e.content, f"{e.importance:.2f}")
    return table


# Recent messages re-rendered on screen when resuming a session with --session.
_RESUME_REPLAY_LIMIT = 8


def _replay_blocks(session: Session, name: str, accent: str) -> list[RenderableType]:
    """Blocks re-rendering the tail of a resumed session so prior turns are visible.

    Display-only: the agent already feeds history into the model separately, so
    this just catches the screen up. Shows the last ``_RESUME_REPLAY_LIMIT``
    messages (noting any older ones hidden).
    """
    msgs = [m for m in session.messages if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]
    if not msgs:
        return []
    shown = msgs[-_RESUME_REPLAY_LIMIT:]
    hidden = len(msgs) - len(shown)
    note = f"Resuming — {len(msgs)} message{'s' if len(msgs) != 1 else ''} so far"
    if hidden:
        note += f", showing the last {len(shown)}"
    blocks: list[RenderableType] = [_note_block(dim(note + ":"))]
    for m in shown:
        if m.role == MessageRole.USER:
            blocks.append(_user_block(m.content))
        else:
            blocks.append(_agent_eyebrow_block(name, accent))
            blocks.append(_render_reply(m.content))
    return blocks


def _footer_line(session_id: UUID, *, memory_off: bool) -> Text:
    """The status-bar line between turns: the session, key hints and memory state."""
    gap = f"   {SEP}   "
    memory = "off" if memory_off else "on"
    return Text.assemble(
        ("session ", TXT3),
        (f"#{str(session_id)[:4]}", TXT2),
        (gap, TXT3),
        ("/help", ACCENT),
        (" commands", TXT3),
        (gap, TXT3),
        ("/exit", ACCENT),
        (" quit", TXT3),
        (gap, TXT3),
        ("Ctrl+C", ACCENT),
        (f" cancel{gap}memory {memory}", TXT3),
    )
