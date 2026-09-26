"""The chat input's parity suite: every behaviour of the prompt_toolkit editor, pinned on Textual.

Each section follows a row of the editor parity matrix. Key-level tests drive a
headless :class:`ArcanaApp` through the Pilot; parser-level tests feed raw
terminal bytes to Textual's ``XTermParser`` to pin which key a terminal sequence
becomes. Pastes are posted to the app (as the driver does), never to the widget.
"""

import time
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from rich.text import Text
from textual import events
from textual._xterm_parser import XTermParser
from textual.message import Message
from textual.widgets import TextArea
from textual.widgets.text_area import Selection

from arcana_cli.tui.app import ArcanaApp
from arcana_cli.tui.chat_input import CONTINUATION, NEWLINE_KEYS, PROMPT, ChatInput, ChatInputPanel
from arcana_cli.tui.completion import session_vocabulary
from arcana_cli.tui.history import AgentHistory
from arcana_cli.tui.slash_registry import slash_registry
from tests.support.tui import TuiHarness, arcana_pilot

AGENTS = ("scout", "scribe", "oracle")

#: The command names a chat input completes when given no other vocabulary.
SESSION_NAMES = session_vocabulary().names


class _RecordingApp(ArcanaApp):
    """An ArcanaApp that keeps every submission the chat input posts."""

    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[ChatInput.Submitted] = []

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        self.submitted.append(message)


class _Chat:
    """A running app with its chat input, and helpers to type into it."""

    def __init__(self, harness: TuiHarness, app: _RecordingApp) -> None:
        self.h = harness
        self.app = app
        self.box = app.chat_input

    @property
    def submitted(self) -> list[str]:
        return [m.text for m in self.app.submitted]

    async def type(self, text: str) -> None:
        await self.h.pilot.press(*text)

    async def press(self, *keys: str) -> None:
        await self.h.pilot.press(*keys)

    async def paste(self, text: str) -> None:
        self.app.post_message(events.Paste(text))
        await self.h.pilot.pause()

    def line(self, y: int) -> str:
        """Row ``y`` of the input as rendered, gutter included."""
        return self.box.render_line(y).text


@asynccontextmanager
async def chat(history: Iterable[str] | AgentHistory = (), agents: Sequence[str] = AGENTS) -> AsyncIterator[_Chat]:
    app = _RecordingApp()
    async with arcana_pilot(app=app) as h:
        recall = history if isinstance(history, AgentHistory) else AgentHistory(entries=list(history))
        app.chat_input.set_history(recall)
        app.chat_input.agent_names = lambda: agents
        yield _Chat(h, app)


def _parse(data: str) -> list[Message]:
    parser = XTermParser()
    return [*parser.feed(data), *parser.feed("")]


def _keys(data: str) -> list[str]:
    return [event.key for event in _parse(data) if isinstance(event, events.Key)]


def _send_keys(c: _Chat, text: str) -> None:
    """Queue a key per character straight to the driver, without waiting between them."""
    driver = c.app._driver
    assert driver is not None
    for char in text:
        event = events.Key(char, char)
        event.set_sender(c.app)
        driver.send_message(event)


def _blob(lines: int, prefix: str = "line") -> str:
    return "\n".join(f"{prefix} {i}" for i in range(lines))


# ── mounting ──────────────────────────────────────────────────────────────


async def test_input_is_mounted_between_live_block_and_status_bar_and_focused():
    async with chat() as c:
        order = list(c.app.screen.children)
        assert order.index(c.app.live) < order.index(c.app.chat_panel) < order.index(c.app.status_bar)
        assert isinstance(c.app.query_one("#chat"), ChatInputPanel)
        assert c.app.focused is c.box


# ── Enter submits · odd trailing "\" + Enter → newline ──────────────────


async def test_enter_submits_and_clears():
    async with chat() as c:
        await c.type("hello")
        await c.press("enter")
        assert c.submitted == ["hello"]
        assert c.box.text == ""


async def test_backslash_enter_inserts_newline_and_drops_the_marker():
    async with chat() as c:
        await c.type("a\\")
        await c.press("enter")
        assert c.box.text == "a\n"
        assert c.submitted == []
        await c.type("b")
        await c.press("enter")
        assert c.submitted == ["a\nb"]


async def test_escaped_backslash_pair_submits():
    async with chat() as c:
        await c.type("a\\\\")
        await c.press("enter")
        assert c.submitted == ["a\\\\"]


async def test_odd_backslash_run_continues():
    async with chat() as c:
        await c.type("a\\\\\\")
        await c.press("enter")
        assert c.box.text == "a\\\\\n"
        assert c.submitted == []


async def test_backslash_rule_reads_the_text_before_the_cursor():
    async with chat() as c:
        await c.type("x\\y")
        await c.press("left")
        await c.press("enter")  # the cursor sits right after the "\"
        assert c.box.text == "x\ny"
        assert c.submitted == []


async def test_blank_enter_submits_empty_text_without_recording_history():
    async with chat() as c:
        await c.press("enter")
        assert c.submitted == [""]
        assert c.box.recall.entries == ()


async def test_a_line_the_filter_refuses_is_submitted_but_not_recorded():
    async with chat() as c:
        c.box.keep_in_history = lambda raw: "secret" not in raw
        await c.type("keep me")
        await c.press("enter")
        await c.type("secret line")
        await c.press("enter")
        assert c.submitted == ["keep me", "secret line"]
        assert c.box.recall.entries == ("keep me",)


async def test_a_generated_commands_action_option_and_agent_complete_from_the_menu():
    async with chat() as c:
        c.box.vocabulary = slash_registry().vocabulary()
        await c.type("/mcp a")
        assert c.box.menu_open
        await c.press("tab")
        assert c.box.text == "/mcp add"
        await c.press(*" --na")
        await c.press("tab")
        assert c.box.text == "/mcp add --name"
        c.box.clear()
        await c.type("/agent edit o")
        await c.press("tab")
        assert c.box.text == "/agent edit oracle"


# ── Ctrl+J, Alt+Enter, Shift+Enter ───────────────────────────────────────


def test_parser_reads_lf_as_ctrl_j_distinct_from_enter():
    assert _keys("\n") == ["ctrl+j"]
    assert _keys("\r") == ["enter"]


def test_parser_reads_kitty_alt_and_shift_enter():
    assert _keys("\x1b[13;3u") == ["alt+enter"]
    assert _keys("\x1b[13;2u") == ["shift+enter"]


@pytest.mark.parametrize("key", sorted(NEWLINE_KEYS))
async def test_newline_keys_insert_a_newline(key):
    async with chat() as c:
        await c.type("a")
        await c.press(key)
        await c.type("b")
        assert c.box.text == "a\nb"
        await c.press("enter")
        assert c.submitted == ["a\nb"]


async def test_newline_key_replaces_a_selection():
    async with chat() as c:
        await c.type("abc")
        await c.press("shift+left", "ctrl+j")
        assert c.box.text == "ab\n"


def test_known_limitation_legacy_alt_enter_parses_as_plain_enter():
    # Without the kitty keyboard protocol, Option+Enter sends ESC CR, which
    # Textual's parser reads as a plain Enter: the modifier is lost (ADR-024 A4,
    # https://github.com/Textualize/textual/issues/6378). When this starts failing,
    # Textual fixed it: make these tests expect a newline, raise the textual lower
    # bound, and drop the limitation from docs/chat.md.
    assert _keys("g\x1b\rh\r") == ["g", "enter", "h", "enter"]


async def test_known_limitation_legacy_alt_enter_submits():
    async with chat() as c:
        for key in _keys("g\x1b\rh\r"):
            await c.press(key)
        assert c.submitted == ["g", "h"]


# ── Paste collapsing ─────────────────────────────────────────────────────


def test_parser_delivers_a_bracketed_paste_as_one_event():
    events_ = _parse("\x1b[200~a\nb\nc\nd\x1b[201~")
    assert [type(e) for e in events_] == [events.Paste]
    assert isinstance(events_[0], events.Paste)
    assert events_[0].text == "a\nb\nc\nd"


async def test_big_paste_collapses_and_expands_on_submit():
    async with chat() as c:
        blob = _blob(6)
        await c.paste(blob)
        assert c.box.text == "[pasted 6 lines]"
        await c.press("enter")
        assert c.submitted == [blob]
        assert c.app.submitted[0].raw == "[pasted 6 lines]"


async def test_small_paste_stays_inline():
    async with chat() as c:
        await c.paste("a\nb")
        assert c.box.text == "a\nb"
        await c.press("enter")
        assert c.submitted == ["a\nb"]


async def test_paste_of_exactly_four_lines_collapses():
    async with chat() as c:
        await c.paste("1\n2\n3\n4")
        assert c.box.text == "[pasted 4 lines]"


async def test_same_size_pastes_are_disambiguated():
    async with chat() as c:
        first, second = _blob(6, "a"), _blob(6, "b")
        await c.paste(first)
        await c.type(" ")
        await c.paste(second)
        assert c.box.text == "[pasted 6 lines] [pasted 6 lines · 2]"
        await c.press("enter")
        assert c.submitted == [f"{first} {second}"]


async def test_paste_registry_is_cleared_after_submit():
    async with chat() as c:
        await c.paste(_blob(6))
        await c.press("enter")
        assert c.box.pastes.expand("[pasted 6 lines]") == "[pasted 6 lines]"
        await c.paste(_blob(6, "z"))
        assert c.box.text == "[pasted 6 lines]"  # no "· 2": the counter restarted


async def test_carriage_return_line_breaks_in_a_paste_are_normalized():
    async with chat() as c:
        await c.paste("a\r\nb\rc\nd")
        await c.press("enter")
        assert c.submitted == ["a\nb\nc\nd"]


async def test_paste_is_undone_in_one_step():
    async with chat() as c:
        await c.type("see ")
        await c.paste(_blob(6))
        assert c.box.text == "see [pasted 6 lines]"
        await c.press("ctrl+z")
        assert c.box.text == "see "


# ── Leading-"/" completion ───────────────────────────────────────────────


async def test_menu_pops_while_typing_a_leading_slash():
    async with chat() as c:
        await c.type("/me")
        assert c.box.menu_open
        assert c.box.menu.items == ("/memory",)
        assert c.box.menu.has_class("-open")


async def test_plain_text_never_pops_the_menu():
    async with chat() as c:
        await c.type("hello /me")
        assert not c.box.menu_open
        await c.press("tab")
        assert not c.box.menu_open
        assert c.box.text == "hello /me"
        assert c.app.focused is c.box  # Tab never moves focus off the input


async def test_tab_cycles_inserting_the_highlighted_item():
    async with chat() as c:
        await c.type("/")
        await c.press("tab")
        assert c.box.text == SESSION_NAMES[0]
        assert c.box.menu.highlighted == 0
        await c.press("tab")
        assert c.box.text == SESSION_NAMES[1]
        await c.press("shift+tab")
        assert c.box.text == SESSION_NAMES[0]


async def test_up_and_down_move_through_an_open_menu():
    async with chat(history=["older entry"]) as c:
        await c.type("/s")
        await c.press("down")
        first = c.box.text
        assert first in {"/switch", "/save"}
        await c.press("down")
        await c.press("up")
        assert c.box.text == first  # Up stepped the menu, not history


async def test_cycling_past_the_last_item_returns_to_the_typed_word():
    async with chat() as c:
        await c.type("/s")
        count = len(c.box.menu.items)
        await c.press(*["tab"] * count)
        await c.press("tab")
        assert c.box.text == "/s"
        assert c.box.menu.highlighted is None


async def test_escape_closes_the_menu_and_restores_the_text():
    async with chat() as c:
        await c.type("/s")
        await c.press("tab", "tab")
        await c.press("escape")
        assert not c.box.menu_open
        assert c.box.text == "/s"
        assert not c.box.menu.has_class("-open")


async def test_tab_reopens_a_closed_menu():
    async with chat() as c:
        await c.type("/he")
        await c.press("escape")
        assert not c.box.menu_open
        await c.press("tab")
        assert c.box.menu_open


async def test_switch_completes_agent_names():
    async with chat() as c:
        await c.type("/switch sc")
        assert c.box.menu.items == ("scout", "scribe")
        await c.press("tab")
        assert c.box.text == "/switch scout"


async def test_menu_closes_when_the_command_is_complete():
    async with chat() as c:
        await c.type("/help")
        assert not c.box.menu_open  # a lone candidate that adds nothing
        await c.type(" x")
        assert not c.box.menu_open


async def test_enter_with_the_menu_open_submits_the_inserted_item():
    async with chat() as c:
        await c.type("/switch or")
        await c.press("tab", "enter")
        assert c.submitted == ["/switch oracle"]
        assert not c.box.menu_open


async def test_moving_the_cursor_away_closes_the_menu():
    async with chat() as c:
        await c.type("/me")
        await c.press("left")
        await c.h.pilot.pause()
        assert not c.box.menu_open


async def test_a_stale_selection_event_does_not_close_the_menu():
    # Under fast typing, the selection event of an earlier key can arrive after
    # later keys moved the cursor on; it must not read as "the cursor moved away".
    async with chat() as c:
        await c.type("/me")
        c.box.post_message(TextArea.SelectionChanged(Selection.cursor((0, 1)), c.box))
        await c.h.pilot.pause()
        assert c.box.menu_open


async def test_undo_recomputes_the_menu():
    async with chat() as c:
        await c.type("/s")
        await c.press("tab")
        await c.press("ctrl+z")
        assert c.box.text == "/s"
        assert c.box.menu_open


# ── Per-agent history, Up/Down, draft restored ──────────────────────────


async def test_up_recalls_newest_first_and_down_restores_the_draft():
    async with chat(history=["first", "second"]) as c:
        await c.type("draft")
        await c.press("up")
        assert c.box.text == "second"
        assert c.box.cursor_location == (0, len("second"))
        await c.press("up")
        assert c.box.text == "first"
        await c.press("up")  # already at the oldest
        assert c.box.text == "first"
        await c.press("down", "down")
        assert c.box.text == "draft"


async def test_up_only_walks_history_from_the_first_row():
    async with chat(history=["old"]) as c:
        await c.type("a")
        await c.press("ctrl+j")
        await c.type("b")
        await c.press("up")
        assert c.box.text == "a\nb"  # the cursor moved up a row
        assert c.box.cursor_location[0] == 0
        await c.press("up")
        assert c.box.text == "old"


async def test_down_only_walks_history_from_the_last_row():
    async with chat(history=["one\ntwo", "newest"]) as c:
        await c.press("up", "up")
        assert c.box.text == "one\ntwo"
        c.box.move_cursor((0, 0))
        await c.press("down")
        assert c.box.text == "one\ntwo"  # moved to the last row
        await c.press("down")
        assert c.box.text == "newest"


async def test_edits_to_a_recalled_entry_are_kept_while_walking():
    async with chat(history=["first", "second"]) as c:
        await c.press("up")
        await c.type("!")
        await c.press("up", "down")
        assert c.box.text == "second!"


async def test_recalled_slash_command_never_pops_the_menu():
    async with chat(history=["/help"]) as c:
        await c.press("up")
        assert c.box.text == "/help"
        assert not c.box.menu_open


async def test_submit_records_history_and_resets_the_walk():
    async with chat(history=["old"]) as c:
        await c.type("new")
        await c.press("enter")
        assert c.box.recall.entries == ("old", "new")
        await c.press("up")
        assert c.box.text == "new"


async def test_repeating_the_newest_entry_is_not_recorded_twice():
    async with chat(history=["same"]) as c:
        await c.type("same")
        await c.press("enter")
        assert c.box.recall.entries == ("same",)


async def test_history_is_scoped_per_agent(tmp_path):
    a, b = uuid4(), uuid4()
    for uid in (a, b):
        (tmp_path / "agents" / str(uid)).mkdir(parents=True)
    AgentHistory.for_agent(a, tmp_path).append("from a")
    async with chat(history=AgentHistory.for_agent(b, tmp_path)) as c:
        await c.press("up")
        assert c.box.text == ""
        c.box.set_history(AgentHistory.for_agent(a, tmp_path))
        await c.press("up")
        assert c.box.text == "from a"


async def test_submit_appends_to_the_current_agent_before_a_switch_runs(tmp_path):
    # prompt_toolkit parity: the /switch line lands in the history of the agent
    # it was typed to, even though the handler re-scopes history straight away.
    a, b = uuid4(), uuid4()
    for uid in (a, b):
        (tmp_path / "agents" / str(uid)).mkdir(parents=True)

    class SwitchingApp(_RecordingApp):
        def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
            super().on_chat_input_submitted(message)
            self.chat_input.set_history(AgentHistory.for_agent(b, tmp_path))

    app = SwitchingApp()
    async with arcana_pilot(app=app) as h:
        app.chat_input.set_history(AgentHistory.for_agent(a, tmp_path))
        await h.pilot.press(*"/switch b", "enter")
        await h.pilot.pause()
    assert AgentHistory.for_agent(a, tmp_path).entries == ("/switch b",)
    assert AgentHistory.for_agent(b, tmp_path).entries == ()
    assert app.chat_input.recall.path == AgentHistory.for_agent(b, tmp_path).path


# ── Autosuggest from history ─────────────────────────────────────────────


async def test_ghost_suggestion_comes_from_history_and_right_accepts_it():
    async with chat(history=["deploy the thing", "hello"]) as c:
        await c.type("dep")
        assert c.box.suggestion == "loy the thing"
        await c.press("right")
        assert c.box.text == "deploy the thing"


async def test_no_suggestion_for_blank_input_or_mid_text_cursor():
    async with chat(history=["deploy the thing"]) as c:
        await c.type(" ")
        assert c.box.suggestion == ""
        await c.press("backspace")
        await c.type("dep")
        await c.press("left")
        await c.h.pilot.pause()
        assert c.box.suggestion == ""


async def test_suggestion_matches_the_last_line_only():
    async with chat(history=["make build"]) as c:
        await c.type("first")
        await c.press("ctrl+j")
        await c.type("ma")
        assert c.box.suggestion == "ke build"


# ── Ctrl+R reverse-i-search ──────────────────────────────────────────────


async def test_ctrl_r_enter_accepts_the_match_without_submitting():
    async with chat(history=["deploy the thing", "hello world"]) as c:
        await c.press("ctrl+r")
        assert c.box.searching
        assert c.box.search_bar.has_class("-active")
        await c.type("deploy")
        assert c.box.text == "deploy the thing"
        await c.press("enter")
        assert not c.box.searching
        assert c.submitted == []
        await c.press("enter")
        assert c.submitted == ["deploy the thing"]


async def test_ctrl_r_again_finds_an_older_match():
    async with chat(history=["echo one", "echo two", "other"]) as c:
        await c.press("ctrl+r")
        await c.type("echo")
        assert c.box.text == "echo two"
        await c.press("ctrl+r")
        assert c.box.text == "echo one"


@pytest.mark.parametrize("key", ["escape", "ctrl+g"])
async def test_cancelling_the_search_restores_the_text(key):
    async with chat(history=["deploy the thing"]) as c:
        await c.type("draft")
        await c.press("ctrl+r")
        await c.type("dep")
        assert c.box.text == "deploy the thing"
        await c.press(key)
        assert not c.box.searching
        assert c.box.text == "draft"
        assert not c.box.search_bar.has_class("-active")


async def test_search_reports_a_failing_query_and_backspace_narrows_back():
    async with chat(history=["deploy"]) as c:
        await c.press("ctrl+r")
        await c.type("dex")
        assert "failing reverse-i-search" in str(c.box.search_bar.render())
        await c.press("backspace")
        assert "failing" not in str(c.box.search_bar.render())
        assert c.box.text == "deploy"


async def test_other_keys_accept_the_search_and_act():
    async with chat(history=["deploy"]) as c:
        await c.press("ctrl+r")
        await c.type("dep")
        await c.press("left")
        assert not c.box.searching
        assert c.box.text == "deploy"
        assert c.box.cursor_location == (0, len("deploy") - 1)


# ── Grows 1→8 lines · busy keeps text · Ctrl+L ──────────────────────────


async def test_input_grows_with_its_lines_up_to_eight():
    async with chat() as c:
        assert c.box.size.height == 1
        await c.paste("1\n2\n3")
        await c.h.pilot.pause()
        assert c.box.size.height == 3
        for _ in range(10):
            await c.press("ctrl+j")
        await c.h.pilot.pause()
        assert c.box.size.height == 8


async def test_enter_while_busy_keeps_the_text():
    async with chat() as c:
        c.box.busy = True
        await c.type("wait")
        await c.press("enter")
        assert c.submitted == []
        assert c.box.text == "wait"
        assert c.box.recall.entries == ()
        c.box.busy = False
        await c.press("enter")
        assert c.submitted == ["wait"]


async def test_ctrl_l_repaints(monkeypatch):
    async with chat() as c:
        repaints: list[bool] = []
        original = c.app.screen.refresh

        def spy(*args: object, repaint: bool = True, layout: bool = False, **kwargs: object):
            repaints.append(repaint and layout)
            return original(*args, repaint=repaint, layout=layout, **kwargs)

        monkeypatch.setattr(c.app.screen, "refresh", spy)
        await c.press("ctrl+l")
        assert True in repaints


# ── Cosmetics: continuation prefix and the menu at the cursor ───────────


async def test_first_line_shows_the_prompt_and_later_lines_a_continuation_mark():
    async with chat() as c:
        await c.type("a")
        await c.press("ctrl+j")
        await c.type("b")
        assert c.line(0).startswith(f"{PROMPT}a")
        mark = f"{CONTINUATION:>{len(PROMPT) - 1}} "
        assert c.line(1).startswith(f"{mark}b")
        assert c.line(1).index(CONTINUATION) == PROMPT.index("›")


async def test_a_soft_wrapped_line_gets_the_continuation_mark():
    async with chat() as c:
        await c.type("x" * 120)
        assert c.box.size.height == 2
        assert c.line(1).lstrip().startswith(CONTINUATION)


async def test_menu_is_anchored_under_the_word_being_completed():
    async with chat() as c:
        await c.type("/switch ")
        await c.h.pilot.pause()
        assert c.box.menu_open
        word_x = c.box.cursor_screen_offset.x  # the completion starts at the cursor
        # Items are padded by one column, so the item text starts right under the word.
        assert c.box.menu.region.x + 1 == word_x
        assert c.box.menu.region.y > c.box.region.y
        await c.press("tab")
        await c.h.pilot.pause()
        assert c.box.menu.region.x + 1 == word_x  # cycling doesn't move it


async def test_menu_is_clamped_to_the_input_width():
    async with arcana_pilot(size=(24, 20), app=_RecordingApp()) as h:
        box = h.app.chat_input
        box.agent_names = lambda: ("a-very-long-agent-name",)
        await h.pilot.press(*"/switch ")
        await h.pilot.pause()
        assert box.menu.region.right <= box.region.right


async def test_menu_scrolls_to_keep_the_highlight_visible():
    async with chat() as c:
        await c.type("/")
        await c.press(*["tab"] * len(SESSION_NAMES))
        rendered = c.box.menu.render()
        assert isinstance(rendered, Text)
        assert SESSION_NAMES[-1] in rendered.plain
        assert SESSION_NAMES[0] not in rendered.plain.split()


# ── Headless dispatch cost ───────────────────────────────────────────────


async def test_key_dispatch_is_under_five_milliseconds():
    async with chat() as c:
        count = 200
        start = time.perf_counter()
        _send_keys(c, "a" * count)
        while len(c.box.text) < count:
            await c.h.pilot.pause()
        per_key = (time.perf_counter() - start) / count
        assert per_key < 0.005
