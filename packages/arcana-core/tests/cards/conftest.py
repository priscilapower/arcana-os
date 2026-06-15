import pytest

from arcana.cards.engine import CardEngine
from arcana.cards.registry import CardRegistry


@pytest.fixture
def registry():
    return CardRegistry()


@pytest.fixture
def engine(registry):
    return CardEngine(registry)
