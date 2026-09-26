"""Shared test doubles for the CLI suite.

* :class:`~tests.support.renderer.RecordingRenderer` — a renderer-port fake that
  records what a command emits and answers its questions from a script, so a
  command coroutine is tested without a terminal.
"""
