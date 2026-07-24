"""Tests for the `arcana memory` group: list / search / inspect / forget / connect / adapters / export.

Unit-ish CliRunner tests over a temp ``~/.arcana`` (real sqlite, no LLM, no
embedder). The global tier is mounted only where needed via an empty
``EmbeddingGateway`` so GLOBAL behaviour is exercised without the embed extra.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.memory as memory_mod
import arcana_cli.commands.tools as tools_mod
from arcana.agents.registry import AgentRegistry
from arcana.memory import EmbeddingGateway, build_federation, paths
from arcana.types import MemoryEntry, MemoryScope, MemoryType
from arcana.types.agent import Agent as AgentRecord
from arcana.types.card import Card
from arcana_cli.main import app

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / ".arcana"
    (h / "agents").mkdir(parents=True)
    monkeypatch.setattr(memory_mod, "ARCANA_HOME", h)
    monkeypatch.setattr(memory_mod, "AGENTS_BASE", h / "agents")
    monkeypatch.setattr(memory_mod, "MEMORY_ADAPTERS_PATH", h / "connections" / "memory-adapters.json")
    monkeypatch.setattr(tools_mod, "AGENTS_BASE", h / "agents")
    # Offline by default: no embedder → private SQLite only.
    monkeypatch.setattr(memory_mod, "_load_embedding_gateway", lambda: None)
    # Confine the ADR-005 guardrails to the test's tmp tree (default root is $HOME).
    monkeypatch.setattr(paths, "_GUARDRAILS", paths.MemoryGuardrails(scope_paths=str(tmp_path)))
    return h


def _agent(home: Path, name: str = "hermit") -> AgentRecord:
    return AgentRegistry(home / "agents").create(name=name, card=Card.HERMIT, model="")


def _seed(home: Path, record: AgentRecord, *entries: MemoryEntry, embedding: EmbeddingGateway | None = None) -> None:
    async def _write() -> None:
        fed = await build_federation(record.id, home=home, embedding=embedding)
        try:
            for entry in entries:
                await fed.write(entry)
        finally:
            await fed.aclose()

    asyncio.run(_write())


def _entry(
    record: AgentRecord,
    content: str,
    *,
    importance: float = 0.5,
    type: MemoryType = MemoryType.SEMANTIC,
    **kw: Any,
) -> MemoryEntry:
    return MemoryEntry(agent_id=record.id, type=type, content=content, importance=importance, **kw)


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


def test_list_orders_by_importance_json(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "low", importance=0.2), _entry(rec, "high", importance=0.9))
    result = runner.invoke(app, ["memory", "list", "--agent", "hermit", "--json"])
    assert result.exit_code == 0
    contents = [e["content"] for e in json.loads(result.output)]
    assert contents == ["high", "low"]


def test_list_table_renders(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "dark roast coffee", importance=0.8))
    result = runner.invoke(app, ["memory", "list", "--agent", "hermit"])
    assert result.exit_code == 0
    assert "dark roast coffee" in result.output


def test_list_empty_store(home: Path):
    _agent(home)
    result = runner.invoke(app, ["memory", "list", "--agent", "hermit", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_list_works_offline_without_embedder(home: Path):
    """The fixture provides no embedder; list must still work (browse path)."""
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "offline works", importance=0.5))
    result = runner.invoke(app, ["memory", "list", "--agent", "hermit", "--json"])
    assert result.exit_code == 0
    assert [e["content"] for e in json.loads(result.output)] == ["offline works"]


def test_list_type_filter(home: Path):
    rec = _agent(home)
    _seed(
        home,
        rec,
        _entry(rec, "a semantic fact", importance=0.6),
        _entry(rec, "an episode", importance=0.6, type=MemoryType.EPISODIC),
    )
    result = runner.invoke(app, ["memory", "list", "--agent", "hermit", "--type", "episodic", "--json"])
    assert [e["content"] for e in json.loads(result.output)] == ["an episode"]


def test_list_unknown_agent_exit_2(home: Path):
    result = runner.invoke(app, ["memory", "list", "--agent", "ghost", "--json"])
    assert result.exit_code == 2


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


def test_search_keyword(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "prefers dark roast coffee"), _entry(rec, "the api base url"))
    result = runner.invoke(app, ["memory", "search", "coffee", "--agent", "hermit", "--mode", "keyword", "--json"])
    assert result.exit_code == 0
    contents = [e["content"] for e in json.loads(result.output)]
    assert contents == ["prefers dark roast coffee"]


def test_search_semantic_degrades_to_keyword_without_embedder(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "coffee beans"))
    result = runner.invoke(app, ["memory", "search", "coffee", "--agent", "hermit"])  # default semantic
    assert result.exit_code == 0
    assert "coffee beans" in result.output


# --------------------------------------------------------------------------
# inspect
# --------------------------------------------------------------------------


def test_inspect_json_has_decay_fields(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "inspect me", importance=0.7))
    mid = json.loads(runner.invoke(app, ["memory", "list", "--agent", "hermit", "--json"]).output)[0]["id"]
    result = runner.invoke(app, ["memory", "inspect", mid, "--agent", "hermit", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["content"] == "inspect me"
    assert "decay_factor" in payload and "effective_importance" in payload


def test_inspect_unknown_id_exit_2(home: Path):
    _agent(home)
    result = runner.invoke(app, ["memory", "inspect", "00000000-0000-0000-0000-000000000000", "--agent", "hermit"])
    assert result.exit_code == 2


def test_inspect_invalid_uuid_exit_1(home: Path):
    _agent(home)
    result = runner.invoke(app, ["memory", "inspect", "not-a-uuid", "--agent", "hermit"])
    assert result.exit_code == 1


# --------------------------------------------------------------------------
# forget
# --------------------------------------------------------------------------


def _first_id(home: Path, agent: str = "hermit") -> str:
    out = runner.invoke(app, ["memory", "list", "--agent", agent, "--json"]).output
    return json.loads(out)[0]["id"]


def test_forget_yes_hard_deletes(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "delete me"))
    mid = _first_id(home)
    result = runner.invoke(app, ["memory", "forget", mid, "--agent", "hermit", "--yes"])
    assert result.exit_code == 0
    assert json.loads(runner.invoke(app, ["memory", "list", "--agent", "hermit", "--json"]).output) == []


def test_forget_prompt_accept(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "confirm delete"))
    mid = _first_id(home)
    result = runner.invoke(app, ["memory", "forget", mid, "--agent", "hermit"], input="y\n")
    assert result.exit_code == 0


def test_forget_prompt_decline_aborts(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "keep me"))
    mid = _first_id(home)
    result = runner.invoke(app, ["memory", "forget", mid, "--agent", "hermit"], input="n\n")
    assert result.exit_code != 0
    # Still there.
    assert len(json.loads(runner.invoke(app, ["memory", "list", "--agent", "hermit", "--json"]).output)) == 1


def test_forget_json_without_yes_errors(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "x"))
    mid = _first_id(home)
    result = runner.invoke(app, ["memory", "forget", mid, "--agent", "hermit", "--json"])
    assert result.exit_code == 1


def test_forget_unknown_id_exit_2(home: Path):
    _agent(home)
    result = runner.invoke(
        app, ["memory", "forget", "00000000-0000-0000-0000-000000000000", "--agent", "hermit", "--yes"]
    )
    assert result.exit_code == 2


def test_forget_archive_soft_deletes(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "archive me"))
    mid = _first_id(home)
    result = runner.invoke(app, ["memory", "forget", mid, "--agent", "hermit", "--archive", "--yes", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["hard"] is False
    # Hidden from list, but inspect (by-id, ignores archived filter) still resolves it.
    assert json.loads(runner.invoke(app, ["memory", "list", "--agent", "hermit", "--json"]).output) == []
    inspect = runner.invoke(app, ["memory", "inspect", mid, "--agent", "hermit", "--json"])
    assert json.loads(inspect.output)["archived"] is True


def test_forget_global_is_refused_exit_3(home: Path, monkeypatch: pytest.MonkeyPatch):
    # Mount a (keyword-only) global tier so a GLOBAL entry is visible.
    monkeypatch.setattr(memory_mod, "_load_embedding_gateway", lambda: EmbeddingGateway([]))
    rec = _agent(home)
    g = _entry(rec, "world truth", importance=0.95, scope=MemoryScope.GLOBAL)
    _seed(home, rec, g, embedding=EmbeddingGateway([]))

    result = runner.invoke(app, ["memory", "forget", str(g.id), "--agent", "hermit", "--yes"])
    assert result.exit_code == 3
    # Survives — still listable in the global scope.
    listed = runner.invoke(app, ["memory", "list", "--agent", "hermit", "--scope", "global", "--json"])
    assert any(e["id"] == str(g.id) for e in json.loads(listed.output))


# --------------------------------------------------------------------------
# connect + adapters
# --------------------------------------------------------------------------


def _vault(tmp_path: Path, *notes: tuple[str, str]) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    for name, body in notes or (("note.md", "a note"),):
        (vault / name).write_text(body)
    return vault


def test_connect_obsidian_writes_registry(home: Path, tmp_path: Path):
    vault = _vault(tmp_path, ("db.md", "postgres"), ("ops.md", "weekly"))
    result = runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault), "--name", "team", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "team" and payload["kind"] == "obsidian"
    registry = json.loads((home / "connections" / "memory-adapters.json").read_text())
    assert registry["connectors"][0]["name"] == "team"


def test_connect_path_traversal_rejected_exit_1(home: Path):
    result = runner.invoke(app, ["memory", "connect", "markdown", "--path", "/etc"])
    assert result.exit_code == 1


def test_connect_missing_dir_rejected(home: Path, tmp_path: Path):
    result = runner.invoke(app, ["memory", "connect", "markdown", "--path", str(tmp_path / "nope")])
    assert result.exit_code == 1


def test_adapters_lists_healthy_with_note_count(home: Path, tmp_path: Path):
    vault = _vault(tmp_path, ("a.md", "one"), ("b.md", "two"))
    runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault), "--name", "team"])
    result = runner.invoke(app, ["memory", "adapters", "--json"])
    assert result.exit_code == 0
    row = json.loads(result.output)[0]
    assert row["name"] == "team" and row["healthy"] is True and row["notes"] == 2


def test_adapters_unreachable_vault_is_unhealthy(home: Path, tmp_path: Path):
    vault = _vault(tmp_path, ("a.md", "one"))
    runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault), "--name", "team"])
    # Remove the folder after registering — the connector stored only a path ref.
    (vault / "a.md").unlink()
    vault.rmdir()
    result = runner.invoke(app, ["memory", "adapters", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)[0]["healthy"] is False


def test_adapters_empty(home: Path):
    result = runner.invoke(app, ["memory", "adapters", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


def test_adapters_corrupt_registry_exits_cleanly(home: Path):
    """A corrupt memory-adapters.json must exit 1 with a message, not a traceback."""
    path = home / "connections" / "memory-adapters.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    result = runner.invoke(app, ["memory", "adapters", "--json"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_connect_corrupt_registry_exits_cleanly(home: Path, tmp_path: Path):
    path = home / "connections" / "memory-adapters.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    vault = _vault(tmp_path, ("a.md", "one"))
    result = runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault)])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_connect_does_not_store_note_contents(home: Path, tmp_path: Path):
    vault = _vault(tmp_path, ("secret.md", "TOPSECRET-classified"))
    runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault), "--name", "team"])
    registry = (home / "connections" / "memory-adapters.json").read_text()
    assert "TOPSECRET" not in registry


# --------------------------------------------------------------------------
# reading a connector by name (--connector, distinct from --pool)
# --------------------------------------------------------------------------


def test_list_connector_reads_notes(home: Path, tmp_path: Path):
    vault = _vault(tmp_path, ("db.md", "the project uses postgres"), ("ops.md", "deploys run weekly"))
    runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault), "--name", "team"])
    result = runner.invoke(app, ["memory", "list", "--connector", "team", "--json"])
    assert result.exit_code == 0
    contents = {e["content"] for e in json.loads(result.output)}
    assert contents == {"the project uses postgres", "deploys run weekly"}


def test_search_connector_keyword(home: Path, tmp_path: Path):
    vault = _vault(tmp_path, ("db.md", "the project uses postgres"), ("ops.md", "deploys run weekly"))
    runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault), "--name", "team"])
    result = runner.invoke(app, ["memory", "search", "postgres", "--connector", "team", "--json"])
    assert result.exit_code == 0
    assert [e["content"] for e in json.loads(result.output)] == ["the project uses postgres"]


def test_list_connector_unknown_exit_2(home: Path):
    result = runner.invoke(app, ["memory", "list", "--connector", "ghost", "--json"])
    assert result.exit_code == 2


def test_list_requires_agent_or_connector(home: Path):
    result = runner.invoke(app, ["memory", "list", "--json"])
    assert result.exit_code == 1


def test_connector_and_agent_are_mutually_exclusive(home: Path, tmp_path: Path):
    vault = _vault(tmp_path, ("a.md", "x"))
    runner.invoke(app, ["memory", "connect", "obsidian", "--vault", str(vault), "--name", "team"])
    _agent(home)
    result = runner.invoke(app, ["memory", "list", "--agent", "hermit", "--connector", "team"])
    assert result.exit_code == 1


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------


def test_export_stdout_markdown(home: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "exported fact", importance=0.8))
    result = runner.invoke(app, ["memory", "export", "--agent", "hermit"])
    assert result.exit_code == 0
    assert "# hermit — Private Memory Export" in result.output
    assert "### exported fact" in result.output


def test_export_out_file_roundtrip(home: Path, tmp_path: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "to a file"))
    out = tmp_path / "dump.md"
    result = runner.invoke(app, ["memory", "export", "--agent", "hermit", "--out", str(out)])
    assert result.exit_code == 0
    assert "### to a file" in out.read_text()


def test_export_out_clobber_guard(home: Path, tmp_path: Path):
    rec = _agent(home)
    _seed(home, rec, _entry(rec, "content"))
    out = tmp_path / "dump.md"
    out.write_text("EXISTING")
    result = runner.invoke(app, ["memory", "export", "--agent", "hermit", "--out", str(out)])
    assert result.exit_code == 1
    assert out.read_text() == "EXISTING"  # untouched
    # --yes overwrites.
    assert runner.invoke(app, ["memory", "export", "--agent", "hermit", "--out", str(out), "--yes"]).exit_code == 0
    assert "EXISTING" not in out.read_text()


def test_export_out_path_traversal_rejected(home: Path):
    _agent(home)
    result = runner.invoke(app, ["memory", "export", "--agent", "hermit", "--out", "/etc/evil.md"])
    assert result.exit_code == 1


def test_export_requires_exactly_one_target(home: Path):
    _agent(home)
    both = runner.invoke(app, ["memory", "export", "--agent", "hermit", "--all"])
    assert both.exit_code == 1
    neither = runner.invoke(app, ["memory", "export"])
    assert neither.exit_code == 1


def test_export_all_covers_every_agent(home: Path):
    a = _agent(home, "hermit")
    b = _agent(home, "magician")
    _seed(home, a, _entry(a, "hermit memory"))
    _seed(home, b, _entry(b, "magician memory"))
    result = runner.invoke(app, ["memory", "export", "--all"])
    assert result.exit_code == 0
    assert "hermit memory" in result.output and "magician memory" in result.output
