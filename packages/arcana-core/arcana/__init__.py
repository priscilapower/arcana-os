"""
Arcana OS — The OS that gives your agents a soul.

Quick start:
    from arcana import Agent, Card
    from arcana.models import OllamaAdapter

    agent = Agent(
        name="researcher",
        card=Card.HERMIT,
        model=OllamaAdapter(model="hermes-3"),
    )
    result = await agent.run("Summarise recent advances in RAG")
"""

from arcana.agents.agent import Agent
from arcana.agents.registry import AgentRegistry
from arcana.agents.session_manager import SessionManager
from arcana.cards.registry import CardRegistry
from arcana.types.card import Card

__all__ = ["Agent", "AgentRegistry", "Card", "CardRegistry", "SessionManager"]
__version__ = "0.1.0"
