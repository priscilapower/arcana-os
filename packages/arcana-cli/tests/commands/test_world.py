"""Tests for the `arcana world route` dry-run command."""

import json

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.run as run_mod
import arcana_cli.commands.world as world_mod
from arcana.agents.registry import AgentRegistry
from arcana.types.card import Card
from arcana_cli.main import app

runner = CliRunner()


@pytest.fixture()
def arcana_home(tmp_path, monkeypatch):
    home = tmp_path / ".arcana"
    (home / "agents").mkdir(parents=True)
    # The world command resolves agents from its own AGENTS_BASE and builds the
    # engine (store + audit paths) via run.build_world_engine, which reads run's
    # ARCANA_HOME — so both module constants need redirecting.
    monkeypatch.setattr(world_mod, "AGENTS_BASE", home / "agents")
    monkeypatch.setattr(run_mod, "ARCANA_HOME", home)
    return home


@pytest.fixture()
def agent_fixture(arcana_home):
    reg = AgentRegistry(arcana_home / "agents")
    return reg.create(name="scout", card=Card.HERMIT, model="ollama/hermes-3")


def test_route_dry_run_prints_decision(agent_fixture, arcana_home):
    result = runner.invoke(app, ["world", "route", "hello there"])
    assert result.exit_code == 0, result.output
    assert "scout" in result.output
    assert "dry run" in result.output.lower()


def test_route_starts_no_session_but_audits(agent_fixture, arcana_home):
    result = runner.invoke(app, ["world", "route", "hello"])
    assert result.exit_code == 0, result.output
    # dry-run resolves and audits, but never opens a session
    sessions = arcana_home / "agents" / str(agent_fixture.id) / "sessions"
    assert not sessions.exists()
    assert (arcana_home / "world" / "routing_audit.jsonl").exists()


def test_route_json_emits_decision(agent_fixture, arcana_home):
    result = runner.invoke(app, ["world", "route", "hello", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["layer"] == "default"
    assert data["resolved_agent_id"] == str(agent_fixture.id)


def test_route_agent_bypass_is_explicit(agent_fixture, arcana_home):
    result = runner.invoke(app, ["world", "route", "hello", "--agent", "scout", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["layer"] == "explicit"
    assert data["matched_rule_id"] is None


def test_route_unknown_agent_exits_nonzero(agent_fixture, arcana_home):
    result = runner.invoke(app, ["world", "route", "hello", "--agent", "ghost"])
    assert result.exit_code != 0
    assert "No agent" in result.output


def test_route_ambiguous_asks_for_agent(arcana_home):
    reg = AgentRegistry(arcana_home / "agents")
    reg.create(name="one", card=Card.HERMIT, model="ollama/hermes-3")
    reg.create(name="two", card=Card.HERMIT, model="ollama/hermes-3")
    result = runner.invoke(app, ["world", "route", "which one?"])
    assert result.exit_code != 0
    assert "--agent" in result.output


def test_route_empty_prompt_exits_nonzero(arcana_home):
    result = runner.invoke(app, ["world", "route", "   "])
    assert result.exit_code != 0
    assert "empty" in result.output.lower()
