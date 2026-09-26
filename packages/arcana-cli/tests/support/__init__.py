"""Shared test doubles for the CLI suite.

* :class:`~tests.support.renderer.RecordingRenderer` — a renderer-port fake that
  records what a command emits and answers its questions from a script, so a
  command coroutine is tested without a terminal.
* :func:`~tests.support.tui.arcana_pilot` — runs the interactive app headless
  under Textual's Pilot, with a ``TextualRenderer`` bound to it.
"""
