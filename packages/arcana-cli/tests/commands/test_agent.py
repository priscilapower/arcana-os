"""Tests for arcana agent commands."""

import json
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# arcana agent create — interactive blend gate
# ---------------------------------------------------------------------------


def test_agent_create_interactive_decline_blend(conn_fixture, arcana_home):
    """Declining blend prompt creates a single-card agent; modifier picker is never shown."""
    with (
        patch("arcana_cli.commands.agent.select_card", return_value=Card.HERMIT),
        patch("arcana_cli.commands.agent.select_cards") as mock_select_cards,
        patch("typer.confirm", return_value=False),
    ):
        result = runner.invoke(app, ["agent", "create"], input="scout\n1\n")
    assert result.exit_code == 0, result.output
    mock_select_cards.assert_not_called()
    agents = AgentRegistry(arcana_home / "agents").list()
    agent = next((a for a in agents if a.name == "scout"), None)
    assert agent is not None
    assert agent.modifier_cards == []


def test_agent_create_interactive_accept_blend(conn_fixture, arcana_home):
    """Accepting blend prompt shows modifier picker and persists selected modifiers."""
    with (
        patch("arcana_cli.commands.agent.select_card", return_value=Card.HERMIT),
        patch("arcana_cli.commands.agent.select_cards", return_value=[Card.FOOL]) as mock_select_cards,
        patch("typer.confirm", return_value=True),
    ):
        result = runner.invoke(app, ["agent", "create"], input="scout\n1\n")
    assert result.exit_code == 0, result.output
    mock_select_cards.assert_called_once()
    agents = AgentRegistry(arcana_home / "agents").list()
    agent = next((a for a in agents if a.name == "scout"), None)
    assert agent is not None
    assert Card.FOOL in agent.modifier_cards


def test_agent_create_modifier_excludes_primary_and_world(conn_fixture, arcana_home):
    """select_cards is called with the primary card and WORLD in the exclude set."""
    with (
        patch("arcana_cli.commands.agent.select_card", return_value=Card.STAR),
        patch("arcana_cli.commands.agent.select_cards", return_value=[]) as mock_select_cards,
        patch("typer.confirm", return_value=True),
    ):
        runner.invoke(app, ["agent", "create"], input="scout\n1\n")
    _, kwargs = mock_select_cards.call_args
    assert kwargs["exclude"] == {Card.STAR, Card.WORLD}


# ---------------------------------------------------------------------------
# arcana agent edit — interactive blend gate
# ---------------------------------------------------------------------------


def test_agent_edit_interactive_decline_modifier_edit(agent_fixture, conn_fixture, arcana_home):
    """Declining modifier edit in interactive flow keeps existing (empty) modifiers."""
    with (
        patch("arcana_cli.commands.agent.select_card", return_value=Card.HERMIT),
        patch("arcana_cli.commands.agent.select_cards") as mock_select_cards,
        patch("typer.confirm", return_value=False),
    ):
        result = runner.invoke(app, ["agent", "edit", "scout"], input="scout\n\n1\n\n")
    assert result.exit_code == 0, result.output
    mock_select_cards.assert_not_called()


def test_agent_edit_modifier_excludes_primary_and_world(agent_fixture, conn_fixture, arcana_home):
    """select_cards in edit is called with the chosen card and WORLD excluded."""
    with (
        patch("arcana_cli.commands.agent.select_card", return_value=Card.FOOL),
        patch("arcana_cli.commands.agent.select_cards", return_value=[]) as mock_select_cards,
        patch("typer.confirm", return_value=True),
    ):
        runner.invoke(app, ["agent", "edit", "scout"], input="scout\n\n1\n\n")
    _, kwargs = mock_select_cards.call_args
    assert kwargs["exclude"] == {Card.FOOL, Card.WORLD}
