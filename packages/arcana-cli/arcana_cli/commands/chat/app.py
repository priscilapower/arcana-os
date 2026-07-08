"""Full-screen layout and the ``arcana chat`` command entry point.

A persistent, full-screen, Claude-Code-style session with a card-configured
agent: a scrolling transcript sits above an input box and a footer that stay
pinned to the bottom of the terminal, so the prompt never floats up the screen.
It reuses the same agent+session+memory path as ``run`` (see
:func:`build_session_runtime`), wrapping it in a prompt_toolkit
:class:`~prompt_toolkit.application.Application` driven by one persistent session.

The layout is assembled around a :class:`_ChatController`, which holds all the
session state; this module only wires it to the terminal.
"""

import asyncio
import contextlib
from uuid import UUID

import typer
from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Float, FloatContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style
from rich.console import Console

from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.models.connection_store import ConnectionStore
from arcana.models.gateway import ModelGateway
from arcana_cli.commands.chat.controller import _H_PAD, _ChatController
from arcana_cli.commands.chat.editor import _agent_history, _build_key_bindings, _SlashCompleter
from arcana_cli.commands.chat.render import _replay_blocks
from arcana_cli.commands.run import build_session_runtime, find_agent
from arcana_cli.constants import ARCANA_HOME
from arcana_cli.ui.theme import ACCENT, SEP, SURFACE, SURFACE_HI, TXT2, TXT3, dim, err

# Plain console for messages printed outside the full-screen app: startup
# validation errors and the resume hint after the app exits.
console = Console()

_PTK_STYLE = Style.from_dict(
    {
        "you": f"bold {ACCENT}",
        "you-cont": TXT3,
        "footer": TXT3,
        "footer.key": ACCENT,
        "footer.val": TXT2,
        "sep": TXT3,
        # Completion menu — kept on the dark palette (prompt_toolkit's default is a
        # bright grey that jars against the canvas). SURFACE_HI reads as a soft
        # raised panel; the current row gets the cyan accent.
        "completion-menu": f"bg:{SURFACE_HI} {TXT2}",
        "completion-menu.completion": f"bg:{SURFACE_HI} {TXT2}",
        "completion-menu.completion.current": f"bold bg:{ACCENT} {SURFACE}",
        "completion-menu.meta.completion": f"bg:{SURFACE_HI} {TXT3}",
        "completion-menu.meta.completion.current": f"bg:{ACCENT} {SURFACE}",
        "scrollbar.background": f"bg:{SURFACE_HI}",
        "scrollbar.button": f"bg:{TXT3}",
    }
)


def _input_prefix(line_number: int, wrap_count: int) -> StyleAndTextTuples:
    """``You › `` on the first line; a dim marker on continued lines."""
    if line_number == 0 and wrap_count == 0:
        return [("class:you", "You › ")]
    return [("class:you-cont", "  … ")]


def _footer_fragments(controller: _ChatController) -> StyleAndTextTuples:
    """The persistent footer: live session id + always-visible key hints."""
    sid = str(controller.session.id)[:4]
    memory = "off" if controller.memory_off else "on"
    d = "class:footer"
    gap = f"   {SEP}   "
    return [
        (d, "session "),
        ("class:footer.val", f"#{sid}"),
        (d, gap),
        ("class:footer.key", "/help"),
        (d, " commands"),
        (d, gap),
        ("class:footer.key", "/exit"),
        (d, " quit"),
        (d, gap),
        ("class:footer.key", "Ctrl+C"),
        (d, f" cancel   {SEP}   memory {memory}"),
    ]


def _global_key_bindings(controller: _ChatController) -> KeyBindings:
    """App-level keys: Ctrl+C (cancel turn / quit), Ctrl+D (quit at an empty prompt)."""
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event: KeyPressEvent) -> None:
        if controller.busy:
            controller.cancel_turn()
        else:
            controller.request_exit()

    @kb.add("c-d")
    def _(event: KeyPressEvent) -> None:
        if not controller.busy and not event.current_buffer.text:
            controller.request_exit()

    return kb


def _build_app(controller: _ChatController) -> Application[None]:
    """Assemble the full-screen application around *controller*.

    Layout is a single HSplit: a scrolling transcript window (anchored to its
    last line so the newest content stays visible), a rule, the input box, an
    on-demand search bar, and the footer. The input + footer never move.
    """
    input_buffer = Buffer(
        multiline=True,
        completer=_SlashCompleter(controller.reg),
        # Pop the slash-command menu as you type — the completer only yields for
        # a leading "/", so ordinary prose never triggers it.
        complete_while_typing=True,
        auto_suggest=AutoSuggestFromHistory(),
        history=_agent_history(controller.record.id),
        accept_handler=controller.on_accept,
        name="chat-input",
    )

    input_control = BufferControl(
        buffer=input_buffer,
        key_bindings=_build_key_bindings(controller.pastes),
    )

    transcript_control = FormattedTextControl(
        lambda: ANSI(controller.transcript.to_ansi(controller.content_width())),
        show_cursor=False,
        focusable=False,
        # Anchor the view to the last rendered line: short transcripts sit at the
        # top, long ones scroll so the newest content stays pinned above the input.
        get_cursor_position=lambda: Point(x=0, y=controller.transcript.last_line),
    )
    # The transcript is the one greedy window: ``ignore_content_height`` lets it
    # shrink to whatever space is left (scrolling internally) so it fills the slack
    # and keeps the input + footer pinned to the bottom, however tall the reply.
    transcript_window = Window(transcript_control, wrap_lines=True, ignore_content_height=True)
    input_window = Window(
        input_control,
        # ``dont_extend_height`` sizes the input strictly to what's typed (1 line,
        # up to 8) so it never absorbs slack and drift up the screen.
        height=Dimension(min=1, max=8),
        dont_extend_height=True,
        wrap_lines=True,
        get_line_prefix=_input_prefix,
    )
    footer_window = Window(
        FormattedTextControl(lambda: _footer_fragments(controller)),
        height=1,
        style="class:footer",
    )
    separator = Window(height=1, char="─", style="class:sep")
    # A matching rule between the input and the footer so they don't crowd each other.
    footer_rule = Window(height=1, char="─", style="class:sep")
    # A blank line below the footer so it isn't glued to the terminal's bottom edge.
    footer_margin = Window(height=1)

    body = HSplit([transcript_window, separator, input_window, footer_rule, footer_window, footer_margin])
    # Gutter columns on each side so text isn't glued to the terminal edge. The
    # SURFACE background is applied here so the whole app sits on one known canvas
    # (every window inherits it) — colours then render predictably regardless of
    # the user's terminal theme.
    padded = VSplit([Window(width=_H_PAD), body, Window(width=_H_PAD)], style=f"bg:{SURFACE}")
    root = FloatContainer(
        padded,
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=8, scroll_offset=1))],
    )
    app: Application[None] = Application(
        layout=Layout(root, focused_element=input_window),
        key_bindings=_global_key_bindings(controller),
        style=_PTK_STYLE,
        full_screen=True,
        mouse_support=False,
    )
    controller.bind_app(app, input_buffer)
    return app


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------
def chat_cmd(
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name or UUID"),
    session_id: str | None = typer.Option(None, "--session", help="Resume a specific session by UUID"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Run stateless — do not load or persist memory"),
) -> None:
    """Start an interactive REPL with a card-configured agent."""

    async def _chat() -> None:
        if not agent:
            # World routing of the first message is deferred to the World Engine.
            console.print(err("The World isn't available yet, pass --agent <name>."))
            raise typer.Exit(1)

        reg = AgentRegistry(ARCANA_HOME / "agents")
        record = find_agent(agent, reg)
        if record is None:
            console.print(err(f"No agent '{agent}'."))
            raise typer.Exit(1)
        if not record.model:
            console.print(
                err(
                    f"No model configured for agent '{record.name}'. "
                    f"Run: arcana agent edit {record.name} --model <provider/model_id>"
                )
            )
            raise typer.Exit(1)

        sm = SessionManager(ARCANA_HOME / "agents")
        if session_id:
            try:
                sid = UUID(session_id)
            except ValueError as e:
                console.print(err(f"Invalid session id: '{session_id}'"))
                raise typer.Exit(1) from e
            session = sm.load(record.id, sid)
            if session is None:
                console.print(err(f"Session '{session_id}' not found for agent '{record.name}'."))
                raise typer.Exit(1)
        else:
            session = sm.start(record.id)

        store = ConnectionStore(ARCANA_HOME / "connections" / "models.json")
        memory_off = no_memory

        async with ModelGateway(connections=store) as gw:
            runtime_agent, federation = await build_session_runtime(reg, record, gw, sm, no_memory=memory_off)
            controller = _ChatController(
                reg=reg,
                gw=gw,
                sm=sm,
                record=record,
                session=session,
                runtime_agent=runtime_agent,
                federation=federation,
                memory_off=memory_off,
            )
            controller.append_header()
            for block in _replay_blocks(session, record.name, controller.accent):
                controller.transcript.append(block)

            app = _build_app(controller)
            try:
                await app.run_async()
            finally:
                with contextlib.suppress(Exception):
                    sm.close(controller.session)
                if controller.federation is not None:
                    await controller.federation.aclose()

        console.print(
            dim(f"session: {str(controller.session.id)[:8]}  ·  resume with  --session {controller.session.id}")
        )

    asyncio.run(_chat())
