"""
Example: Blending two cards — The Hermit + The Empress.

A researcher with a warm, generous tone. Depth of The Hermit,
richness of The Empress. Temperature blends to ~0.50.

Run with:
    uv run python examples/multi_card_blend.py
"""

import asyncio

from arcana import Agent, Card
from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry


async def main() -> None:
    registry = get_registry()
    engine = CardEngine(registry)

    # Show the blend
    config = engine.resolve(Card.HERMIT, modifiers=[Card.EMPRESS])
    print("=== Card Blend ===")
    print(f"Blend:       {config.blend_note}")
    print(f"Temperature: {config.temperature}")
    print(f"Memory weights:")
    print(f"  episodic:   {config.memory_weights.episodic}")
    print(f"  semantic:   {config.memory_weights.semantic}")
    print(f"  procedural: {config.memory_weights.procedural}")
    print(f"  preference: {config.memory_weights.preference}")
    print()
    print("=== Generated System Prompt ===")
    print(config.system_prompt)


if __name__ == "__main__":
    asyncio.run(main())
