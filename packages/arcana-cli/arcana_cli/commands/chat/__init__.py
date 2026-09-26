"""The interactive chat session — ``arcana chat``, and bare ``arcana``.

The command is split across focused modules:

- :mod:`.command` — the Typer command and :func:`~.command.open_chat`: picks the
  opening agent and session, then starts the app. Loads no Textual itself.
- :mod:`.app` — :class:`~.app.ChatApp` (the session's keys) and :func:`~.app.run_chat`.
- :mod:`.controller` — :class:`~.controller._ChatController`, all session state + turn logic.
- :mod:`.render` — the transcript blocks: header, user bubble, streamed reply, tables, replay.
"""

from arcana_cli.commands.chat.command import NO_MOUSE_HELP, chat_cmd, open_chat

__all__ = ["NO_MOUSE_HELP", "chat_cmd", "open_chat"]
