"""The generic question dialogs: free text, yes/no, and a pick from a list.

Each is a :class:`~textual.screen.ModalScreen` whose dismiss value is the answer,
so a caller awaits it with ``push_screen_wait``. Every dialog is cancellable:
Esc dismisses it with its cancel value (``None``, or ``False`` for a yes/no), so
an awaiting caller always gets an answer back.
"""

from collections.abc import Sequence
from typing import Any

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from arcana_cli.ui.renderer.port import Choice, Question


class PromptScreen(ModalScreen[str | None]):
    """Asks a :class:`~arcana_cli.ui.renderer.port.Question`; dismisses with the answer, or ``None`` on Esc.

    A secret question uses a password input and never shows its default. An empty
    submission takes the default when there is one. A validator rejection is
    shown under the input and the dialog stays open for another try; the message
    is the validator's own, which must never quote a secret answer.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, question: Question) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        q = self._question
        placeholder = q.default if q.default is not None and not q.secret else ""
        with Vertical(classes="dialog"):
            yield Static(escape(q.prompt), classes="dialog-title")
            yield Input(placeholder=placeholder, password=q.secret, id="answer")
            yield Static("", classes="dialog-error", id="problem")
            yield Static("Enter to submit · Esc to cancel", classes="dialog-hint")

    @on(Input.Submitted, "#answer")
    def _submitted(self, event: Input.Submitted) -> None:
        q = self._question
        answer = event.value
        if not answer and q.default is not None:
            answer = q.default
        problem = q.validator(answer) if q.validator is not None else None
        if problem is not None:
            self.query_one("#problem", Static).update(escape(problem))
            return
        self.dismiss(answer)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question: ``y`` / ``n`` / Enter for the default; Esc answers no."""

    BINDINGS = [
        Binding("y", "answer(True)", "Yes", show=False),
        Binding("n", "answer(False)", "No", show=False),
        Binding("enter", "answer_default", "Default", show=False, priority=True),
        Binding("escape", "answer(False)", "Cancel", show=False),
    ]

    def __init__(self, text: str, *, default: bool = False) -> None:
        super().__init__()
        self._text = text
        self._default = default

    def compose(self) -> ComposeResult:
        hint = "Y/n" if self._default else "y/N"
        with Vertical(classes="dialog"):
            yield Static(escape(self._text), classes="dialog-title")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Yes", id="yes", compact=True)
                yield Button("No", id="no", compact=True)
            yield Static(f"{hint} · Esc for no", classes="dialog-hint")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)

    def action_answer_default(self) -> None:
        self.dismiss(self._default)


class SelectScreen(ModalScreen[int | None]):
    """Pick one choice with Enter or a click; dismisses with its index into ``choices``, or ``None`` on Esc.

    Disabled choices are shown greyed out and can't be picked. The cursor starts
    on ``initial`` when given.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, choices: Sequence[Choice[Any]], *, initial: int | None = None, title: str = "") -> None:
        super().__init__()
        self._choices = choices
        self._initial = initial
        self._title = title or "Select one"

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(escape(self._title), classes="dialog-title")
            yield OptionList(
                *(Option(escape(c.label), disabled=c.disabled) for c in self._choices),
                id="choices",
            )
            yield Static("Enter to pick · Esc to cancel", classes="dialog-hint")

    def on_mount(self) -> None:
        if self._initial is not None:
            self.query_one("#choices", OptionList).highlighted = self._initial

    @on(OptionList.OptionSelected, "#choices")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)

    def action_cancel(self) -> None:
        self.dismiss(None)


class MultiSelectScreen(ModalScreen[list[int] | None]):
    """Pick several choices: Space (or a click) toggles, Enter submits, Esc cancels.

    Dismisses with the picked indexes into ``choices`` in list order, or ``None``
    on Esc. Disabled choices are shown greyed out and can't be toggled.
    ``initial`` holds the pre-selected indexes; the cursor starts on the first.
    A submission over ``max_items`` is refused with a message and the dialog
    stays open.
    """

    BINDINGS = [
        Binding("enter", "submit", "Submit", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        choices: Sequence[Choice[Any]],
        *,
        initial: Sequence[int] = (),
        title: str = "",
        max_items: int | None = None,
    ) -> None:
        super().__init__()
        self._choices = choices
        self._initial = [i for i in initial if not choices[i].disabled]
        self._title = title or "Select"
        self._max_items = max_items
        # Held directly: a subscripted generic can't be a query_one type filter.
        self._list = SelectionList[int](
            *(Selection(escape(c.label), i, i in self._initial, disabled=c.disabled) for i, c in enumerate(choices)),
            id="choices",
        )

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(escape(self._title), classes="dialog-title")
            yield self._list
            yield Static("", classes="dialog-error", id="problem")
            yield Static("Space to toggle · Enter to submit · Esc to cancel", classes="dialog-hint")

    def on_mount(self) -> None:
        if self._initial:
            self._list.highlighted = self._initial[0]

    def action_submit(self) -> None:
        picked = sorted(self._list.selected)
        if self._max_items is not None and len(picked) > self._max_items:
            self.query_one("#problem", Static).update(f"Pick at most {self._max_items}.")
            return
        self.dismiss(picked)

    def action_cancel(self) -> None:
        self.dismiss(None)
