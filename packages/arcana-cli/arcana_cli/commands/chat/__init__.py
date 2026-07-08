"""The ``arcana chat`` interactive REPL.

The command is split across focused modules:

- :mod:`.editor` — input editing: key bindings, paste collapsing, completion, history.
- :mod:`.render` — turning model output and session state into rendered transcript blocks.
- :mod:`.controller` — :class:`~.controller._ChatController`, all session state + turn logic.
- :mod:`.app` — the full-screen layout and the :func:`~.app.chat_cmd` entry point.
"""

from arcana_cli.commands.chat.app import chat_cmd

__all__ = ["chat_cmd"]
