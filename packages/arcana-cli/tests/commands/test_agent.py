"""Tests for arcana agent commands."""

import json

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.agent as agent_mod
from arcana.agents.registry import AgentRegistry
from arcana.types.card import Card
from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli.main import app

runner = CliRunner()


@pytest.fixture()
def arcana_home(tmp_path, monkeypatch):
    home = tmp_path / ".arcana"
    home.mkdir()
    (home / "agents").mkdir()
    (home / "connections").mkdir()
    monkeypatch.setattr(agent_mod, "AGENTS_BASE", home / "agents")
    monkeypatch.setattr(agent_mod, "CONNECTIONS_PATH", home / "connections" / "models.json")
    return home


@pytest.fixture()
def conn_fixture(arcana_home):
    conn = ModelConnection(
        name="ollama/hermes-3",
        provider=ModelProvider.OLLAMA,
        model_id="hermes-3",
        endpoint="http://localhost:11434",
    )
    path = arcana_home / "connections" / "models.json"
    path.write_text(json.dumps([json.loads(conn.model_dump_json())]))
    return conn


@pytest.fixture()
def agent_fixture(arcana_home, conn_fixture):
    reg = AgentRegistry(arcana_home / "agents")
    return reg.create(name="scout", card=Card.HERMIT, model_connection_id=conn_fixture.id)


# ---------------------------------------------------------------------------
# arcana agent list
# ---------------------------------------------------------------------------


def test_agent_list_no_agents(arcana_home):
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    assert "No agents yet" in result.output


def test_agent_list_shows_table(agent_fixture, arcana_home):
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    assert "scout" in result.output
    assert "the-hermit" in result.output


# ---------------------------------------------------------------------------
# arcana agent create
# ---------------------------------------------------------------------------


def test_agent_create_with_flags(conn_fixture, arcana_home):
    result = runner.invoke(
        app,
        ["agent", "create", "--name", "scout", "--card", "the-hermit", "--model", "ollama/hermes-3"],
    )
    assert result.exit_code == 0, result.output
    assert "scout" in result.output
    agents = AgentRegistry(arcana_home / "agents").list()
    assert any(a.name == "scout" for a in agents)


def test_agent_create_unknown_card_exits_nonzero(conn_fixture, arcana_home):
    result = runner.invoke(
        app,
        ["agent", "create", "--name", "scout", "--card", "not-a-real-card", "--model", "ollama/hermes-3"],
    )
    assert result.exit_code != 0


def test_agent_create_missing_connection_exits_nonzero(arcana_home):
    result = runner.invoke(
        app,
        ["agent", "create", "--name", "scout", "--card", "the-hermit", "--model", "missing"],
    )
    assert result.exit_code != 0
    assert "No connection" in result.output


def test_agent_create_world_card_exits_nonzero(conn_fixture, arcana_home):
    result = runner.invoke(
        app,
        ["agent", "create", "--name", "world-agent", "--card", "the-world", "--model", "ollama/hermes-3"],
    )
    assert result.exit_code != 0
    assert "reserved" in result.output


# ---------------------------------------------------------------------------
# arcana agent show
# ---------------------------------------------------------------------------


def test_agent_show(agent_fixture, arcana_home):
    result = runner.invoke(app, ["agent", "show", "scout"])
    assert result.exit_code == 0
    assert "scout" in result.output
    assert "the-hermit" in result.output


def test_agent_show_not_found(arcana_home):
    result = runner.invoke(app, ["agent", "show", "ghost"])
    assert result.exit_code != 0
    assert "No agent" in result.output


# ---------------------------------------------------------------------------
# arcana agent edit
# ---------------------------------------------------------------------------


def test_agent_edit_with_flags(agent_fixture, conn_fixture, arcana_home):
    result = runner.invoke(
        app,
        [
            "agent",
            "edit",
            "scout",
            "--name",
            "ranger",
            "--card",
            "the-fool",
            "--model",
            "ollama/hermes-3",
            "--tags",
            "test,research",
            "--description",
            "A test agent",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ranger" in result.output
    agents = AgentRegistry(arcana_home / "agents").list()
    updated = next((a for a in agents if a.name == "ranger"), None)
    assert updated is not None
    assert updated.card == Card.FOOL
    assert "test" in updated.tags
    assert updated.description == "A test agent"


def test_agent_edit_not_found(arcana_home):
    result = runner.invoke(app, ["agent", "edit", "ghost", "--name", "specter"])
    assert result.exit_code != 0


def test_agent_edit_invalid_card(agent_fixture, conn_fixture, arcana_home):
    result = runner.invoke(
        app,
        [
            "agent",
            "edit",
            "scout",
            "--card",
            "not-a-card",
            "--model",
            "ollama/hermes-3",
            "--name",
            "scout",
            "--tags",
            "",
        ],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# arcana agent delete
# ---------------------------------------------------------------------------


def test_agent_delete_with_yes_flag(agent_fixture, arcana_home):
    result = runner.invoke(app, ["agent", "delete", "scout", "--yes"])
    assert result.exit_code == 0
    assert "scout" in result.output
    agents = AgentRegistry(arcana_home / "agents").list()
    assert not any(a.name == "scout" for a in agents)


def test_agent_delete_not_found(arcana_home):
    result = runner.invoke(app, ["agent", "delete", "ghost", "--yes"])
    assert result.exit_code != 0
