"""Fixtures for the interactive-app tests.

``tui`` is :func:`tests.support.tui.arcana_pilot`, the factory itself rather than
a running app: the app must start and stop inside the test's own task (Textual
keeps its active-app context in context variables), so a test enters it with
``async with tui() as h:``.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest

from tests.support.tui import TuiHarness, arcana_pilot


@pytest.fixture
def tui() -> Callable[..., AbstractAsyncContextManager[TuiHarness]]:
    return arcana_pilot
