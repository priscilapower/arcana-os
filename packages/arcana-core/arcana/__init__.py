"""
Arcana OS — The OS that gives your agents a soul.

Quick start:
    from arcana import Agent, Card, World
    from arcana.memory import MemoryFederation, SharedMemoryPool, SQLiteAdapter
    from arcana.knowledge import ObsidianConnector
    from arcana.models import OllamaAdapter

    # Shared memory pool — multiple agents can read/write
    pool = SharedMemoryPool(
        name="my-project",
        adapter=SQLiteAdapter("~/.arcana/memory/shared/my-project.db"),
    )

    # External knowledge — Arcana reads but does not own
    obsidian = ObsidianConnector(vault_path="~/Documents/MyVault")

    researcher = Agent(
        name="researcher",
        card=Card.HERMIT,
        model=OllamaAdapter(model="hermes-3"),
        memory=MemoryFederation(
            private=SQLiteAdapter("~/.arcana/agents/researcher/memory.db"),
            shared_pools=[pool],
        ),
        knowledge=[obsidian],
    )
"""

from arcana.agents.agent import Agent
from arcana.cards.registry import CardRegistry
from arcana.types.card import Card
from arcana.world.engine import World

__all__ = ["Agent", "Card", "CardRegistry", "World"]
__version__ = "0.1.0"
