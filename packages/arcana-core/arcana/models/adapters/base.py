"""ModelAdapter ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any


@dataclass
class CompletionRequest:
    system: str
    messages: list[dict[str, str]]
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: list[dict[str, Any]] | None = None
    stream: bool = False


@dataclass
class CompletionResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, Any]] | None = None
    stop_reason: str = "end_turn"


@dataclass
class ModelHealth:
    healthy: bool
    model_id: str
    message: str = ""


class ModelAdapter(ABC):
    """Every LLM backend implements this interface."""

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        ...

    @abstractmethod
    async def stream(
        self, request: CompletionRequest
    ) -> AsyncGenerator[str, None]:
        ...

    @abstractmethod
    async def health_check(self) -> ModelHealth:
        ...
