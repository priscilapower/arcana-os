"""The interactive session's Textual app: the shell, its widgets, dialogs and stylesheet.

* :mod:`~arcana_cli.tui.app` — :class:`ArcanaApp` and its inline run.
* :mod:`~arcana_cli.tui.widgets` — the transcript, live block and status bar.
* :mod:`~arcana_cli.tui.chat_input` — the chat prompt box, its completion menu and search bar.
* :mod:`~arcana_cli.tui.history` — per-agent input history in prompt_toolkit's file format.
* :mod:`~arcana_cli.tui.completion` — slash-command completion.
* :mod:`~arcana_cli.tui.screens` — the modal question dialogs.
* :mod:`~arcana_cli.tui.theme_tcss` — the stylesheet and theme generated from ``ui/theme.py``.
* :mod:`~arcana_cli.tui.config` — the ``ui`` block of ``config.json`` and ``ARCANA_TUI_*`` tunables.

The renderer-port adapter over the app is
:class:`arcana_cli.ui.renderer.textual_renderer.TextualRenderer`.
"""

from arcana_cli.tui.app import ArcanaApp

__all__ = ["ArcanaApp"]
