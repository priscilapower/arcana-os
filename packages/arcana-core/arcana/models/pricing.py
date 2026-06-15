"""Token usage accounting and cost metering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arcana.types.model import ModelConnection


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total: int
    cost: float | None = None

    @classmethod
    def from_tokens(
        cls,
        input_tokens: int,
        output_tokens: int,
        cost: float | None = None,
    ) -> Usage:
        return cls(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total=input_tokens + output_tokens,
            cost=cost,
        )


@dataclass(frozen=True)
class CostEvent:
    """Emitted by the gateway after every completed call. Not persisted — sink aggregates it.

    ``metadata`` is copied verbatim from the originating ``CompletionRequest``. The gateway
    never reads or validates it; callers use it to attribute cost to a session or agent.
    """

    model: str
    usage: Usage
    estimated: bool = False
    priced: bool = True
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, str] | None = None


# (input_per_1k_tokens, output_per_1k_tokens) in USD
# Prices drift — keep them as overridable data, not constants.
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "anthropic/claude-opus-4-8": (0.015, 0.075),
    "anthropic/claude-sonnet-4-6": (0.003, 0.015),
    "anthropic/claude-haiku-4-5": (0.00025, 0.00125),
    "openai/gpt-4o": (0.0025, 0.010),
    "openai/gpt-4o-mini": (0.00015, 0.0006),
    "openai/gpt-4-turbo": (0.010, 0.030),
    "openai/gpt-3.5-turbo": (0.0005, 0.0015),
}


class PricingTable:
    """Token cost lookup by ``provider/model_id``. Local (Ollama) models are always $0."""

    def __init__(self, data: dict[str, tuple[float, float]] | None = None) -> None:
        self._data: dict[str, tuple[float, float]] = dict(_DEFAULT_PRICES) if data is None else data

    def cost(
        self,
        model_key: str,
        input_tokens: int,
        output_tokens: int,
        conn: ModelConnection | None = None,
    ) -> float | None:
        """Return the cost in USD, or ``None`` if the model has no known price.

        Lookup priority:
        1. Per-connection ``cost_per_1k_*`` overrides (if both are set).
        2. Global pricing table entry.
        3. ``0.0`` for local/Ollama models (known to be free).
        4. ``None`` — price is unknown; callers should flag the event as unpriced.
        """
        if conn is not None:
            if conn.cost_per_1k_input_tokens is not None and conn.cost_per_1k_output_tokens is not None:
                return (
                    conn.cost_per_1k_input_tokens * input_tokens + conn.cost_per_1k_output_tokens * output_tokens
                ) / 1000

        entry = self._data.get(model_key)
        if entry:
            return (entry[0] * input_tokens + entry[1] * output_tokens) / 1000

        if conn is not None and conn.is_local:
            return 0.0

        return None

    def override(self, model_key: str, input_per_1k: float, output_per_1k: float) -> None:
        self._data[model_key] = (input_per_1k, output_per_1k)


DEFAULT_PRICING = PricingTable()
