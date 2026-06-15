"""Agent — the central object. Wires card + model + memory + tools together."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry
from arcana.models.adapters.base import CompletionRequest
from arcana.models.gateway import ModelGateway
from arcana.observability import SessionEvent, get_audit_log, get_metrics, get_tracer
from arcana.types.card import Card
from arcana.types.memory import MemoryAdapter, MemoryEntry, MemoryQuery, MemoryType
from arcana.types.session import MessageRole, Session, SessionStatus, SessionTrigger

if TYPE_CHECKING:
    from arcana.agents.session_manager import SessionManager


class Agent:
    """
    A configured AI agent. Assign a tarot card — get a soul.

    Usage:
        async with ModelGateway(ConnectionStore()) as gw:
            agent = Agent(
                name="researcher",
                card=Card.HERMIT,
                gateway=gw,
                model="ollama/hermes-3",
            )
            result = await agent.run("summarize advances in RAG")
    """

    def __init__(
        self,
        name: str,
        card: Card,
        gateway: ModelGateway,
        model: str,
        description: str = "",
        modifier_cards: list[Card] | None = None,
        memory: MemoryAdapter | None = None,
        soul: str | None = None,
        system_prompt_override: str | None = None,
        id: UUID | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self.id = id or uuid4()
        self.name = name
        self.card = card
        self.modifier_cards = modifier_cards or []
        self._gateway = gateway
        self._model = model
        self.memory = memory
        self.soul = soul
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

        with get_tracer().start_as_current_span("session.run") as span:
            span.set_attribute("arcana.agent.name", self.name)
            span.set_attribute("arcana.card", self.card.value)
            span.set_attribute("arcana.model", self._model)
            span.set_attribute("arcana.session_id", str(session.id))

            memory_context = await self._retrieve_memory_context(prompt)
            system = self._build_system(memory_context, context)

            request = CompletionRequest(
                system=system,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._temperature,
                metadata={"session_id": str(session.id), "agent_id": str(self.id)},
            )
            response = await self._gateway.complete(self._model, request)

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

            span.set_attribute("arcana.input_tokens", response.input_tokens)
            span.set_attribute("arcana.output_tokens", response.output_tokens)
            span.set_attribute("arcana.duration_ms", session.duration_ms)

        self._emit_session_event(session)
        await self._extract_memory(prompt, response.content, session)
        self._sessions.append(session)
        return response.content

    async def stream(
        self,
        prompt: str,
        context: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a response token by token. Records a session with token totals."""
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
            stream=True,
            metadata={"session_id": str(session.id), "agent_id": str(self.id)},
        )

        content_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        try:
            async for chunk in self._gateway.stream(self._model, request):
                content_parts.append(chunk.text)
                input_tokens += chunk.input_tokens
                output_tokens += chunk.output_tokens
                yield chunk.text
        finally:
            full_content = "".join(content_parts)
            if self._session_manager:
                self._session_manager.append(session, MessageRole.ASSISTANT, full_content)
            else:
                session.add_message(MessageRole.ASSISTANT, full_content)
            session.total_input_tokens = input_tokens
            session.total_output_tokens = output_tokens
            if self._session_manager:
                self._session_manager.close(session, SessionStatus.COMPLETED)
            else:
                session.close(SessionStatus.COMPLETED)
            self._sessions.append(session)
            self._emit_session_event(session)

    @property
    def card_config(self):  # type: ignore[return]
        """The resolved AgentConfig — temperature, memory weights, etc."""
        return self._config

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _emit_session_event(self, session: Session) -> None:
        """Append a SessionEvent to the audit log and record metrics. Non-fatal."""
        try:
            audit = get_audit_log()
            if audit is not None:
                audit.append(
                    SessionEvent(
                        session_id=str(session.id),
                        agent_id=str(self.id),
                        agent_name=self.name,
                        card=self.card.value,
                        modifier_cards=[c.value for c in self.modifier_cards],
                        model=self._model,
                        input_tokens=session.total_input_tokens,
                        output_tokens=session.total_output_tokens,
                        duration_ms=session.duration_ms,
                        status=session.status.value,
                    )
                )
            get_metrics().record_session(
                card=self.card.value,
                model=self._model,
                status=session.status.value,
                input_tokens=session.total_input_tokens,
                output_tokens=session.total_output_tokens,
                duration_ms=session.duration_ms,
            )
        except Exception:
            pass

    def _build_system(
        self,
        memory_context: str,
        extra_context: str | None,
    ) -> str:
        parts = [self._system_prompt]
        if self.soul:
            parts.append(f"\n\n─── USER CONTEXT ───\n{self.soul}\n─── END USER CONTEXT ───")
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
