"""Agent — the central object. Wires card + model + memory + tools together."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry
from arcana.memory.extraction import (
    DEFAULT_MIN_CONFIDENCE_TO_STORE,
    HeuristicExtractor,
    MemoryExtractor,
    filter_storable,
)
from arcana.models.adapters.base import CompletionRequest, MessageParam, ToolCallResult, ToolParam
from arcana.models.gateway import ModelGateway
from arcana.observability import SessionEvent, get_audit_log, get_metrics, get_tracer
from arcana.tools.config import DEFAULT_MAX_TOOL_ITERATIONS
from arcana.types._utils import JsonValue
from arcana.types.card import Card
from arcana.types.memory import MemoryAdapter, MemoryQuery
from arcana.types.session import MessageRole, Session, SessionStatus, SessionTrigger, ToolCall
from arcana.types.tool import ToolResult, ToolSubscription

if TYPE_CHECKING:
    from arcana.agents.session_manager import SessionManager
    from arcana.tools.gateway import ToolGateway

logger = logging.getLogger("arcana.agents.agent")

# Oldest turns are dropped from the replayed context window only; full transcript stays on disk.
MAX_HISTORY_TURNS = 20

# Validates a tool call's JSON arguments into a genuinely-typed params dict for
# the session record; a non-object payload degrades to an empty dict.
_PARAMS_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])

# Retrieved entries below this confidence are never injected into the prompt.
# Mirrors ``MemoryProfile.min_confidence_for_context``; the store-side floor
# is ``min_confidence_to_store`` (entries below it are never persisted).
DEFAULT_MIN_CONFIDENCE_FOR_CONTEXT = 0.5


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
        extractor: MemoryExtractor | None = None,
        min_confidence_to_store: float = DEFAULT_MIN_CONFIDENCE_TO_STORE,
        min_confidence_for_context: float = DEFAULT_MIN_CONFIDENCE_FOR_CONTEXT,
        summarise_on_close: bool = True,
        tool_gateway: ToolGateway | None = None,
        tool_subscriptions: list[str] | None = None,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
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
        # Extraction strategy — heuristic by default (deterministic, model-free).
        self._extractor = extractor or HeuristicExtractor()
        self._min_confidence_to_store = min_confidence_to_store
        self._min_confidence_for_context = min_confidence_for_context
        self._summarise_on_close = summarise_on_close

        # Tool execution — off by default. With a gateway and subscriptions, run()
        # exposes only the subscribed tools and loops model→tool→model up to the cap.
        self._tool_gateway = tool_gateway
        self._tool_subscriptions = [ToolSubscription(qualified_name=s) for s in (tool_subscriptions or [])]
        self._max_tool_iterations = max(1, max_tool_iterations)

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
        *,
        session: Session | None = None,
        context: str | None = None,
    ) -> str:
        """Run a single prompt. Returns the assistant's response.

        Pass *session* to resume a prior conversation; omit to start a new one.
        """
        if session is None:
            session = (
                self._session_manager.start(self.id, SessionTrigger.USER)
                if self._session_manager
                else Session(agent_id=self.id)
            )

        session.add_message(MessageRole.USER, prompt)

        history: list[MessageParam] = [
            MessageParam(role=m.role.value, content=m.content)
            for m in session.messages
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT)
        ]
        history = history[-(MAX_HISTORY_TURNS * 2) :]

        with get_tracer().start_as_current_span("session.run") as span:
            span.set_attribute("arcana.agent.name", self.name)
            span.set_attribute("arcana.card", self.card.value)
            span.set_attribute("arcana.model", self._model)
            span.set_attribute("arcana.session_id", str(session.id))

            memory_context = await self._retrieve_memory_context(prompt)
            system = self._build_system(memory_context, context)

            tools, allowed = await self._resolve_tools()

            # Tool loop: complete, run any requested tools, feed results back, and
            # re-complete until the model stops calling tools or the cap is hit.
            # With no tools this runs exactly once — byte-for-byte the old path.
            final_content = ""
            input_tokens = 0
            output_tokens = 0
            iterations = 0
            cap_hit = False
            for _ in range(self._max_tool_iterations):
                iterations += 1
                request = CompletionRequest(
                    system=system,
                    messages=history,
                    temperature=self._temperature,
                    tools=tools or None,
                    metadata={"session_id": str(session.id), "agent_id": str(self.id)},
                )
                response = await self._gateway.complete(self._model, request)
                input_tokens += response.input_tokens
                output_tokens += response.output_tokens
                final_content = response.content

                if not response.tool_calls:
                    break
                if iterations >= self._max_tool_iterations:
                    # Final allowed pass: don't run tools whose results could never
                    # be sent back — just stop with the model's last text.
                    cap_hit = True
                    break

                calls = list(response.tool_calls)
                history.append(MessageParam(role="assistant", content=response.content, tool_calls=calls))
                # tool_calls only come back when tools were exposed, which requires
                # a gateway — narrow the Optional for the type checker.
                tool_gateway = self._tool_gateway
                assert tool_gateway is not None
                results = await asyncio.gather(*(tool_gateway.dispatch(call, allowed=allowed) for call in calls))
                for call, result in zip(calls, results, strict=True):
                    history.append(self._tool_result_message(call, result))
                    self._record_tool_call(session, call, result)

            if cap_hit:
                logger.warning(
                    "tool loop hit max iterations (%d) for agent %s; returning last text",
                    self._max_tool_iterations,
                    self.name,
                )
                span.set_attribute("arcana.tool.cap_hit", True)

            session.add_message(MessageRole.ASSISTANT, final_content)
            session.total_input_tokens = input_tokens
            session.total_output_tokens = output_tokens

            await self._extract_memory(prompt, final_content, session)
            await self._close_session(session, SessionStatus.COMPLETED)

            span.set_attribute("arcana.input_tokens", input_tokens)
            span.set_attribute("arcana.output_tokens", output_tokens)
            span.set_attribute("arcana.tool.iterations", iterations)
            span.set_attribute("arcana.duration_ms", session.duration_ms)

        self._emit_session_event(session)
        self._sessions.append(session)
        return final_content

    async def stream(
        self,
        prompt: str,
        *,
        session: Session | None = None,
        context: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a response token by token. Records a session with token totals.

        Pass *session* to resume a prior conversation; omit to start a new one.
        """
        if session is None:
            session = (
                self._session_manager.start(self.id, SessionTrigger.USER)
                if self._session_manager
                else Session(agent_id=self.id)
            )

        session.add_message(MessageRole.USER, prompt)

        history: list[MessageParam] = [
            MessageParam(role=m.role.value, content=m.content)
            for m in session.messages
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT)
        ]
        history = history[-(MAX_HISTORY_TURNS * 2) :]

        memory_context = await self._retrieve_memory_context(prompt)
        system = self._build_system(memory_context, context)

        request = CompletionRequest(
            system=system,
            messages=history,
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
            session.add_message(MessageRole.ASSISTANT, full_content)
            session.total_input_tokens = input_tokens
            session.total_output_tokens = output_tokens
            await self._extract_memory(prompt, full_content, session)
            await self._close_session(session, SessionStatus.COMPLETED)
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

    async def _resolve_tools(self) -> tuple[list[ToolParam], set[str]]:
        """Resolve subscriptions into request tools + the permitted name set.

        Returns empty results (and never calls the model gateway) when there is
        no tool gateway or no subscriptions — keeping the default path a single
        ``complete()`` with ``tools=None``. When the model can't call tools the
        gateway resolves nothing, so tools are silently omitted.
        """
        if self._tool_gateway is None or not self._tool_subscriptions:
            return [], set()
        supports = await self._gateway.supports_tools(self._model)
        tools = self._tool_gateway.tools_for(self._tool_subscriptions, supports)
        allowed = {tool["name"] for tool in tools}
        return tools, allowed

    @staticmethod
    def _tool_result_message(call: ToolCallResult, result: ToolResult) -> MessageParam:
        """Serialize a ``ToolResult`` into a ``tool`` turn the model can read."""
        if result.success:
            output = result.output
            content = output if isinstance(output, str) else json.dumps(output)
        else:
            content = f"Error: {result.error or 'tool failed'}"
        return MessageParam(
            role="tool",
            content=content,
            tool_call_id=call["id"],
            name=call["function"]["name"],
        )

    @staticmethod
    def _record_tool_call(session: Session, call: ToolCallResult, result: ToolResult) -> None:
        """Append a ``ToolCall`` record to the session for observability."""
        try:
            params = _PARAMS_ADAPTER.validate_json(call["function"]["arguments"] or "{}")
        except ValidationError:
            params = {}
        result_payload: dict[str, JsonValue] | None = {"output": result.output} if result.success else None
        session.tool_calls.append(
            ToolCall(
                tool_name=call["function"]["name"],
                params=params,
                result=result_payload,
                error=result.error,
                duration_ms=result.duration_ms,
            )
        )

    async def _retrieve_memory_context(self, prompt: str) -> str:
        if not self.memory:
            return ""
        query = MemoryQuery(text=prompt, limit=5, min_confidence=self._min_confidence_for_context)
        entries = await self.memory.search(query)
        if not entries:
            return ""
        return "\n".join(f"- {e.content}" for e in entries)

    async def _extract_memory(self, prompt: str, response: str, session: Session) -> None:
        """Extract typed, confidence-scored memories from this turn and persist them.

        Best-effort: the extractor produces candidate entries, sub-threshold ones
        are dropped, and survivors fan out through the federation. Any
        failure is logged and swallowed — extraction never fails a user-facing run.
        """
        if not self.memory:
            return
        try:
            candidates = await self._extractor.extract(prompt, response, session)
            for entry in filter_storable(candidates, self._min_confidence_to_store):
                await self.memory.write(entry)
                session.memories_extracted.append(entry.id)
        except Exception:
            logger.warning("memory extraction failed; continuing without it", exc_info=True)

    async def _close_session(self, session: Session, status: SessionStatus) -> None:
        """Close the session, summarising and consolidating when a manager is wired.

        With a ``SessionManager`` the close path optionally distils a summary into
        ``session.summary`` and writes one consolidated memory; without one it is a
        plain in-memory close.
        """
        if self._session_manager:
            await self._session_manager.close_and_summarise(
                session,
                status,
                summarise=self._summarise_on_close,
                memory=self.memory,
                extractor=self._extractor,
            )
        else:
            session.close(status)
