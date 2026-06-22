"""AgentRegistry — CRUD for agent records persisted to ~/.arcana/agents/."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from arcana.agents.agent import Agent as RuntimeAgent
from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry
from arcana.context.soul import read_soul
from arcana.models.gateway import ModelGateway
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana.types.memory import MemoryAdapter

if TYPE_CHECKING:
    from arcana.agents.session_manager import SessionManager
    from arcana.models.connection_store import ConnectionStore


def _default_base() -> Path:
    return Path.home() / ".arcana" / "agents"


class AgentRegistry:
    """
    Manages agent records (types.Agent) on disk.

    Each record lives at ~/.arcana/agents/{id}/agent.json.
    The registry does not manage runtime agents (agents.Agent) directly —
    use build_runtime() to reconstruct one from a stored record and an adapter.
    """

    def __init__(self, base_dir: Path | None = None, connections: ConnectionStore | None = None) -> None:
        self._base = base_dir or _default_base()
        self._connections = connections

    def _connection_store(self) -> ConnectionStore:
        if self._connections is None:
            from arcana.models.connection_store import ConnectionStore

            self._connections = ConnectionStore()
        return self._connections

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        card: Card,
        model: str = "",
        *,
        description: str = "",
        modifier_cards: list[Card] | None = None,
        system_prompt_override: str | None = None,
        tags: list[str] | None = None,
    ) -> AgentRecord:
        """Create a new agent record, resolve card config, and persist to disk."""
        card_registry = get_registry()
        engine = CardEngine(card_registry)
        config = engine.resolve(card, modifier_cards or [])

        # Prompt and temperature are denormalized: computed once here and stored
        # on the record. build_runtime() feeds them back as overrides, so agents
        # are stable across card definition changes. If a definition is ever
        # revised, existing agents will keep stale prompts until re-created or
        # explicitly edited. Acceptable for MVP given card defs are treated as final.
        record = AgentRecord(
            name=name,
            card=card,
            modifier_cards=modifier_cards or [],
            model=model,
            description=description,
            system_prompt=system_prompt_override or config.system_prompt,
            temperature=config.temperature,
            tags=tags or [],
        )
        self.save(record)
        return record

    def get(self, agent_id: UUID) -> AgentRecord | None:
        """Return an agent record by ID, or None if not found."""
        path = self._agent_path(agent_id)
        if not path.exists():
            return None
        return self._load_record(path)

    def list(self) -> list[AgentRecord]:
        """Return all non-archived agent records, sorted by name."""
        records: list[AgentRecord] = []
        if not self._base.exists():
            return records
        for agent_dir in self._base.iterdir():
            if not agent_dir.is_dir():
                continue
            agent_json = agent_dir / "agent.json"
            if not agent_json.exists():
                continue
            try:
                record = self._load_record(agent_json)
                if not record.is_archived:
                    records.append(record)
            except Exception as exc:
                print(f"warning: skipping {agent_json}: {exc}", file=sys.stderr)
        return sorted(records, key=lambda r: r.name)

    def save(self, record: AgentRecord) -> None:
        """Persist an agent record to disk."""
        agent_dir = self._base / str(record.id)
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "agent.json").write_text(record.model_dump_json(indent=2))

    def delete(self, agent_id: UUID) -> None:
        """Soft-delete: mark the agent as archived so list() excludes it."""
        record = self.get(agent_id)
        if record is None:
            raise FileNotFoundError(f"Agent {agent_id} not found.")
        self.save(record.model_copy(update={"is_archived": True}))

    # ------------------------------------------------------------------
    # Runtime agent construction
    # ------------------------------------------------------------------

    def build_runtime(
        self,
        record: AgentRecord,
        gateway: ModelGateway,
        *,
        memory: MemoryAdapter | None = None,
        session_manager: SessionManager | None = None,
        soul: str | None = None,
    ) -> RuntimeAgent:
        """Reconstruct a runtime Agent from a stored record and gateway."""
        return RuntimeAgent(
            id=record.id,
            name=record.name,
            card=record.card,
            gateway=gateway,
            model=record.model,
            description=record.description,
            modifier_cards=record.modifier_cards,
            memory=memory,
            soul=soul if soul is not None else read_soul(),
            system_prompt_override=record.system_prompt,
            session_manager=session_manager,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _agent_path(self, agent_id: UUID) -> Path:
        return self._base / str(agent_id) / "agent.json"

    def _load_record(self, path: Path) -> AgentRecord:
        """Load an agent record, migrating legacy model_connection_id if present."""
        raw: dict[str, object] = _json.loads(path.read_text())
        if "model_connection_id" in raw and not raw.get("model"):
            raw = self._migrate_legacy_model(raw)
            try:
                path.write_text(_json.dumps(raw, indent=2, default=str))
            except Exception:
                pass
        return AgentRecord.model_validate(raw)

    def _migrate_legacy_model(self, raw: dict[str, object]) -> dict[str, object]:
        """Convert a model_connection_id UUID FK to a provider:name/default_model string."""
        out: dict[str, object] = dict(raw)
        try:
            conn_id = UUID(str(out["model_connection_id"]))
            conn = self._connection_store().get_by_id(conn_id)
            if conn is not None:
                if conn.default_model:
                    out["model"] = f"{conn.provider}:{conn.name}/{conn.default_model}"
                else:
                    out["model"] = f"{conn.provider}:{conn.name}"
            else:
                out["model"] = ""
        except Exception:
            out["model"] = ""
        return out
