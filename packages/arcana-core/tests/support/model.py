"""A scripted model that drives ``Agent.run`` deterministically.

The ``Agent`` talks to a :class:`~arcana.models.gateway.ModelGateway`, not a raw
``ModelAdapter``, so this fakes *that* seam: a programmed sequence of turns — each
either a set of tool calls or a final block of text — returned one per
``complete()`` call, while recording every :class:`CompletionRequest` it was
handed. That record (:attr:`ScriptedModel.seen`) is what lets a test assert *what
the model was offered*: ``tools=None`` on the no-subscription path, an MCP tool
under its wire-safe name, the tool results fed back on the next turn.

It is the single loop-driver reused across the integration and contract tiers, so
those tests script the model the same way instead of each re-rolling a slightly
different ``MagicMock`` with ``side_effect``.

Build a script from the two turn helpers::

    ScriptedModel([calls(tool_call("web_search", q="cats")), text("done")])

The first ``complete()`` asks for a ``web_search`` call; once its result is fed
back, the second returns the final text and the loop stops.
"""

import json
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Any

from arcana.models.adapters.base import (
    CompletionRequest,
    CompletionResponse,
    FunctionCall,
    ModelChunk,
    ToolCallResult,
)


def tool_call(name: str, *, call_id: str = "call-1", **arguments: Any) -> ToolCallResult:
    """One tool call as the model would emit it — ``arguments`` JSON-encoded.

    Keyword arguments become the call's arguments payload, matching the wire
    shape (a JSON string) the gateway validates and the adapter receives.
    """
    return ToolCallResult(
        id=call_id,
        type="function",
        function=FunctionCall(name=name, arguments=json.dumps(arguments)),
    )


@dataclass(frozen=True, slots=True)
class Turn:
    """One scripted model turn: either tool calls to run, or final text."""

    tool_calls: list[ToolCallResult] | None
    content: str
    input_tokens: int
    output_tokens: int


def calls(*tool_calls: ToolCallResult, content: str = "", tokens: tuple[int, int] = (3, 2)) -> Turn:
    """A turn that requests ``tool_calls`` (one or many, dispatched together)."""
    return Turn(tool_calls=list(tool_calls), content=content, input_tokens=tokens[0], output_tokens=tokens[1])


def text(content: str = "done", *, tokens: tuple[int, int] = (4, 6)) -> Turn:
    """A terminal turn: plain text, no tool calls, so the loop stops."""
    return Turn(tool_calls=None, content=content, input_tokens=tokens[0], output_tokens=tokens[1])


class ScriptedModel:
    """A ``ModelGateway`` stand-in that replays ``script`` turn by turn.

    Duck-types the three methods ``Agent`` uses — ``supports_tools``,
    ``complete``, ``stream`` — so it drops into the ``gateway=`` slot directly.
    ``complete`` advances the script and records the request in :attr:`seen`;
    running past the end is an explicit error, never a silent extra turn (a fake
    that quietly kept answering would mask a loop that never terminates).
    """

    def __init__(self, script: Sequence[Turn], *, supports_tools: bool = True) -> None:
        self._script = list(script)
        self._turn = 0
        self._supports_tools = supports_tools
        #: Every ``CompletionRequest`` handed to ``complete``/``stream``, in order.
        self.seen: list[CompletionRequest] = []

    async def supports_tools(self, model: str) -> bool:
        return self._supports_tools

    async def complete(self, model: str, request: CompletionRequest) -> CompletionResponse:
        self.seen.append(request)
        turn = self._next("complete")
        return CompletionResponse(
            content=turn.content,
            tool_calls=turn.tool_calls,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )

    async def stream(self, model: str, request: CompletionRequest) -> AsyncGenerator[ModelChunk, None]:
        self.seen.append(request)
        turn = self._next("stream")
        words = turn.content.split()
        for i, word in enumerate(words):
            is_last = i == len(words) - 1
            yield ModelChunk(
                text=word + " ",
                input_tokens=turn.input_tokens if is_last else 0,
                output_tokens=turn.output_tokens if is_last else 0,
            )

    @property
    def completions(self) -> int:
        """How many turns the model has been driven through so far."""
        return len(self.seen)

    def _next(self, caller: str) -> Turn:
        if self._turn >= len(self._script):
            raise AssertionError(
                f"ScriptedModel ran out of turns: {caller}() was called {self._turn + 1} times "
                f"but the script has {len(self._script)}. The agent asked for more turns than scripted."
            )
        turn = self._script[self._turn]
        self._turn += 1
        return turn
