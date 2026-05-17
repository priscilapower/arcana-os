"""
Example: A research agent powered by The Hermit card.

Run with:
    uv run python examples/research_agent.py

Requirements:
    - Ollama running locally with hermes-3 (or change the model)
    - OR set ANTHROPIC_API_KEY and use AnthropicAdapter
"""

import asyncio
from pathlib import Path

from arcana import Agent, Card
from arcana.memory import MemoryFederation, SQLiteAdapter
from arcana.models import OllamaAdapter


async def main() -> None:
    # Memory: SQLite backed, persists to ~/.arcana/examples/
    memory_path = Path.home() / ".arcana" / "examples" / "research_agent.db"
    federation = MemoryFederation(
        adapters=[SQLiteAdapter(db_path=memory_path)],
        agent_card=Card.HERMIT,
    )
    await federation.connect()

    # Agent: The Hermit — deep researcher, temp 0.35, semantic-heavy memory
    agent = Agent(
        name="researcher",
        card=Card.HERMIT,
        model=OllamaAdapter(model="hermes-3"),
        memory=federation,
    )

    print(f"Agent: {agent.name}")
    print(f"Card:  {agent.card.value}")
    print(f"Temp:  {agent.card_config.temperature}")
    print(f"Config: {agent.card_config.blend_note}")
    print("-" * 60)

    prompts = [
        "What are the key tradeoffs between RAG and fine-tuning for LLMs?",
        "Based on what we just discussed, which approach would you recommend for a small startup?",
    ]

    for prompt in prompts:
        print(f"\nUser: {prompt}")
        print("Agent: ", end="", flush=True)

        # Stream the response
        async for chunk in agent.stream(prompt):
            print(chunk, end="", flush=True)
        print()

    await federation.close()


if __name__ == "__main__":
    asyncio.run(main())
