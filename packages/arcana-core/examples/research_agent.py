"""
Example: A research agent powered by The Hermit card.

Run with:
    uv run python packages/arcana-core/examples/research_agent.py

Requirements:
    - Ollama running locally with hermes-3 (or change the model)
    - OR configure an Anthropic connection and use "anthropic/claude-3-5-haiku-latest"
"""

import asyncio

from arcana import Agent, Card
from arcana.models import ConnectionStore, ModelGateway


async def main() -> None:
    async with ModelGateway(ConnectionStore()) as gw:
        # Agent: The Hermit — deep researcher, temp 0.35, semantic-heavy memory weights
        agent = Agent(
            name="researcher",
            card=Card.HERMIT,
            gateway=gw,
            model="ollama/hermes-3",
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


if __name__ == "__main__":
    asyncio.run(main())
