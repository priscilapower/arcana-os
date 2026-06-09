"""Agent — the central object. Wires card + model + memory + tools together."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry
from arcana.models.adapters.base import CompletionRequest, ModelAdapter
from arcana.types.card import Card
from arcana.types.memory import MemoryAdapter, MemoryEntry, MemoryQuery, MemoryType
from arcana.types.session import MessageRole, Session, SessionStatus, SessionTrigger

if TYPE_CHECKING:
    from arcana.agents.session_manager import SessionManager


class Agent:
    """
    A configured AI agent. Assign a tarot card — get a soul.

    Usage:
        agent = Agent(
            name="researcher",
            card=Card.HERMIT,
            model=OllamaAdapter(model="hermes-3"),
        )
        result = await agent.run("summarize advances in RAG")
    """

    def __init__(
        self,
        name: str,
        card: Card,
        model: ModelAdapter,
        description: str = "",
        modifier_cards: list[Card] | None = None,
        memory: MemoryAdapter | None = None,
        system_prompt_override: str | None = None,
        id: UUID | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self.id = id or uuid4()
        self.name = name
        self.card = card
        self.modifier_cards = modifier_cards or []
        self.model = model
        self.memory = memory
        self.description = description
        self._session_manager = session_manager

        # Resolve config from card(s)
        registry = get_registry()
        engine = CardEngine(registry)
        self._config = engine.resolve(card, self.modifier_cards)

        # Allow full system prompt override
        self._system_prompt = system_prompt_override or self._config.system_prompt
        self._temperature = self._config.temperature

        self._sessions: list[Session] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        context: str | None = None,
    ) -> str:
        """Run a single prompt. Returns the assistant's response."""
        if self._session_manager:
            session = self._session_manager.start(self.id, SessionTrigger.USER)
            self._session_manager.append(session, MessageRole.USER, prompt)
        else:
            session = Session(agent_id=self.id)
            session.add_message(MessageRole.USER, prompt)

        memory_context = await self._retrieve_memory_context(prompt)
        system = self._build_system(memory_context, context)

        request = CompletionRequest(
            system=system,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
        )
        response = await self.model.complete(request)

        if self._session_manager:
            self._session_manager.append(session, MessageRole.ASSISTANT, response.content)
        else:
            session.add_message(MessageRole.ASSISTANT, response.content)

        session.total_input_tokens = response.input_tokens
        session.total_output_tokens = response.output_tokens

        if self._session_manager:
            self._session_manager.close(session, SessionStatus.COMPLETED)
        else:
            session.close(SessionStatus.COMPLETED)

        await self._extract_memory(prompt, response.content, session)
        self._sessions.append(session)
        return response.content

    async def stream(
        self,
        prompt: str,
        context: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a response token by token."""
        memory_context = await self._retrieve_memory_context(prompt)
        system = self._build_system(memory_context, context)

        request = CompletionRequest(
            system=system,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            stream=True,
        )
        async for chunk in self.model.stream(request):
            yield chunk.text

    @property
    def card_config(self):  # type: ignore[return]
        """The resolved AgentConfig — temperature, memory weights, etc."""
        return self._config

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_system(
        self,
        memory_context: str,
        extra_context: str | None,
    ) -> str:
        parts = [self._system_prompt]
        if memory_context:
            parts.append(f"\n\n## Relevant Memory\n{memory_context}")
        if extra_context:
            parts.append(f"\n\n## Context\n{extra_context}")
        return "\n".join(parts)

    async def _retrieve_memory_context(self, prompt: str) -> str:
        if not self.memory:
            return ""
        query = MemoryQuery(text=prompt, limit=5)
        entries = await self.memory.search(query)
        if not entries:
            return ""
        return "\n".join(f"- {e.content}" for e in entries)

    async def _extract_memory(self, prompt: str, response: str, session: Session) -> None:
        """Persist a basic episodic memory of this exchange."""
        if not self.memory:
            return
        entry = MemoryEntry(
            agent_id=self.id,
            type=MemoryType.EPISODIC,
            content=f"User asked: {prompt[:200]}\nResponse summary: {response[:300]}",
            source_session_id=session.id,
            importance=0.5,
        )
        await self.memory.write(entry)
