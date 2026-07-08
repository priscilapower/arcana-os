"""Transcript rendering for the chat REPL.

rich owns every visual (Markdown replies, panels, tables); this module turns
model output and session state into rich renderables, then flattens them to ANSI
strings the full-screen layout can display. It covers three things: splitting a
reply into dimmed reasoning + answer, the block builders (header, user bubble,
tables, resume replay), and :class:`_Transcript`, the per-block-cached list of
rendered blocks shown in the scrolling region.
"""

import re
from io import StringIO

from rich import box
from rich.console import Console, Group, RenderableType
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
from arcana_cli.commands.chat.editor import _SLASH_COMMANDS
from arcana_cli.ui.mathtext import normalize_math
from arcana_cli.ui.theme import (
    ACCENT,
    AMBER,
    GREEN,
    MARKDOWN_THEME,
    PROMPT,
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

# Package-internal exports — consumed by the controller, the app layout, and the
# tests. Declared so the split (these helpers now live one import away from their
# callers) doesn't read as dead code under strict unused-symbol checks.
__all__ = [
    "_Transcript",
    "_agent_eyebrow_block",
    "_card_table",
    "_header_block",
    "_help_table",
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


# ---------------------------------------------------------------------------
# Transcript — rich renderables rendered to ANSI for the full-screen layout.
# ---------------------------------------------------------------------------
# OSC-8 hyperlink sequences (emitted by Markdown links) confuse prompt_toolkit's
# ANSI parser, so they're stripped from rendered output.
_OSC8_RE = re.compile(r"\x1b\]8;[^\x1b\a]*(?:\x07|\x1b\\)")


def _render_ansi(renderable: RenderableType, width: int) -> str:
    """Render one rich renderable to an ANSI string at *width* columns."""
    buf = StringIO()
    Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=max(20, width),
        theme=MARKDOWN_THEME,
        highlight=False,
    ).print(renderable)
    return _OSC8_RE.sub("", buf.getvalue())


class _Transcript:
    """The ordered list of rendered blocks shown in the scrolling region.

    Blocks are rich renderables; :meth:`to_ansi` renders them to a single ANSI
    string at the current width, caching per block so streaming only re-renders
    the one in-progress reply. :meth:`update_last` swaps the last block in place —
    that's how a reply grows token by token.
    """

    def __init__(self) -> None:
        self._blocks: list[RenderableType] = []
        self._cache: list[str | None] = []
        self._width = 0
        self._lines = 0

    def append(self, block: RenderableType) -> None:
        self._blocks.append(block)
        self._cache.append(None)

    def update_last(self, block: RenderableType) -> None:
        if not self._blocks:
            self.append(block)
            return
        self._blocks[-1] = block
        self._cache[-1] = None

    def clear(self) -> None:
        self._blocks.clear()
        self._cache.clear()

    def to_ansi(self, width: int) -> str:
        if width != self._width:  # a resize invalidates every cached render
            self._width = width
            self._cache = [None] * len(self._blocks)
        out: list[str] = []
        for i, block in enumerate(self._blocks):
            cached = self._cache[i]
            if cached is None:
                cached = _render_ansi(block, width)
                self._cache[i] = cached
            out.append(cached)
        text = "".join(out)
        self._lines = text.count("\n")
        return text

    @property
    def last_line(self) -> int:
        """Row index of the final line — the transcript anchors its cursor here."""
        return max(0, self._lines - 1)

    def plain_text(self) -> str:
        """Uncoloured concatenation of every block — for tests and assertions."""
        buf = StringIO()
        out = Console(file=buf, width=100, theme=MARKDOWN_THEME, no_color=True)
        for block in self._blocks:
            out.print(block)
        return buf.getvalue()


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
    newline_keys = f"{hl('Shift+Enter')} or {backslash_enter}"
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


def _help_table() -> RenderableType:
    table = make_table("In-session commands")
    table.add_column("", style=f"bold {ACCENT}")
    table.add_column("")
    for name, desc in _SLASH_COMMANDS:
        table.add_row(name, desc)
    return table


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
