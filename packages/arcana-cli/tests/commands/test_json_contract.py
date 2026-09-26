"""The uniform ``--json`` contract, checked across every command group.

Every scriptable command takes ``--json``; under it stdout carries exactly one
JSON document — the result, or ``{"error": {"code", "message"[, "details"]}}``
with ``code`` equal to the exit status — and nothing else. Lists are arrays of
objects, ids are strings, and no document carries a secret.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import typer.main
from typer.testing import CliRunner

from arcana_cli._render import EXIT_DENIED, EXIT_ERROR, EXIT_NOT_FOUND
from arcana_cli.main import app
from tests.support.world import (
    MEMORY_ID,
    World,
    no_home,
    seed_agent,
    seed_memory,
    seed_ollama,
    seed_server,
    seed_server_with_tools,
    seed_soul,
    seed_twins,
)

runner = CliRunner()

#: Commands with no ``--json`` mode, and why.
NO_JSON = {
    "chat": "the interactive session",
    "soul edit": "hands the terminal to $EDITOR",
}


def _commands(group: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every runnable command under ``group`` (typer vendors click, so groups are recognised by shape)."""
    for name, command in sorted(group.commands.items()):
        path = f"{prefix}{name}"
        if hasattr(command, "commands"):
            if command.invoke_without_command:  # the group itself runs (arcana cards)
                yield path, command
            yield from _commands(command, f"{path} ")
        else:
            yield path, command


def _has_json(command: Any) -> bool:
    return any("--json" in getattr(p, "opts", []) for p in command.params)


def test_every_command_takes_json_but_the_interactive_ones():
    root = typer.main.get_command(app)
    missing = sorted(path for path, command in _commands(root) if not _has_json(command))
    assert missing == sorted(NO_JSON)


# ── results ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Case:
    args: list[str]
    setup: Callable[[World], Any] = lambda _w: None
    check: Callable[[Any], None] = lambda _doc: None
    code: int = 0
    env: dict[str, str] = field(default_factory=dict)


def _keys(*names: str) -> Callable[[Any], None]:
    def check(doc: Any) -> None:
        rows = doc if isinstance(doc, list) else [doc]
        assert rows, "expected at least one object"
        for row in rows:
            assert set(names) <= set(row), f"missing {set(names) - set(row)} in {row}"

    return check


RESULTS: dict[str, Case] = {
    "status": Case(["status"], seed_ollama, _keys("home", "agents", "connections")),
    "init": Case(["init"], no_home, lambda d: d == {"home": d["home"], "created": True}),
    "init_exists": Case(["init"], check=lambda d: d["created"] is False),
    "agent_list": Case(["agent", "list"], seed_agent, _keys("id", "name", "card", "model", "status")),
    "agent_list_empty": Case(["agent", "list"], check=lambda d: d == []),
    "agent_show": Case(
        ["agent", "show", "scout"],
        seed_agent,
        _keys("id", "name", "card", "modifier_cards", "temperature", "tags", "created_at", "system_prompt"),
    ),
    "agent_create": Case(
        ["agent", "create", "--name", "ranger", "--card", "hermit", "--model", "ollama/hermes-3"],
        check=lambda d: (d["name"], d["card"], d["model"]) == ("ranger", "the-hermit", "ollama/hermes-3"),
    ),
    "agent_edit": Case(
        [
            "agent",
            "edit",
            "scout",
            "--name",
            "ranger",
            "--description",
            "",
            "--card",
            "fool",
            "--model",
            "m/x",
            "--tags",
            "",
        ],
        seed_agent,
        lambda d: (d["name"], d["card"], d["tags"]) == ("ranger", "the-fool", []),
    ),
    "agent_delete": Case(["agent", "delete", "scout", "--yes"], seed_agent, _keys("deleted", "id")),
    "providers_list": Case(
        ["providers", "list"], seed_ollama, _keys("id", "name", "provider", "default_model", "endpoint", "auth_type")
    ),
    "providers_show": Case(
        ["providers", "show", "ollama/hermes-3"], seed_ollama, _keys("name", "headers", "credential", "created_at")
    ),
    "providers_add": Case(
        ["providers", "add", "-p", "ollama", "-m", "hermes-3", "-n", "local", "-e", "http://localhost:11434"],
        check=lambda d: (d["name"], d["action"]) == ("local", "added"),
    ),
    "providers_edit": Case(
        ["providers", "edit", "ollama/hermes-3", "--base-url", "http://gpu:11434", "--no-verify"],
        seed_ollama,
        lambda d: (d["endpoint"], d["health"]) == ("http://gpu:11434", None),
    ),
    "providers_remove": Case(["providers", "remove", "ollama/hermes-3", "--yes"], seed_ollama, _keys("removed")),
    "providers_remove_dependents": Case(
        ["providers", "remove", "ollama/hermes-3", "--yes"],
        lambda w: (seed_ollama(w), seed_agent(w)),
        lambda d: d["aborted"] == "dependents" and d["dependents"][0]["agent"] == "scout",
        code=EXIT_ERROR,
    ),
    "cards_catalog": Case(["cards"], check=lambda d: len(d) == 21 and set(d[0]) == {"id", "number", "name", "role"}),
    "cards_show": Case(["cards", "show", "hermit"], check=lambda d: (d["id"], d["number"]) == ("the-hermit", 9)),
    "soul_show": Case(["soul", "show"], seed_soul, lambda d: d["exists"] and d["content"].startswith("# Pri")),
    "soul_show_none": Case(["soul", "show"], check=lambda d: (d["exists"], d["content"]) == (False, None)),
    "mcp_list": Case(["mcp", "list"], seed_server_with_tools, _keys("name", "transport", "tools", "changed_tools")),
    "mcp_approve_nothing": Case(
        ["mcp", "approve", "notion-mcp", "--tool", "search_pages"],
        seed_server_with_tools,
        lambda d: d["approved"] == [],
    ),
    "memory_export": Case(["memory", "export", "--agent", "hermit"], seed_memory, _keys("exported", "bytes")),
    "memory_inspect": Case(
        ["memory", "inspect", str(MEMORY_ID), "--agent", "hermit"], seed_memory, _keys("id", "decay_factor")
    ),
    "tools_subscribe": Case(
        ["tools", "subscribe", "scout", "builtin/web_search"], seed_agent, lambda d: d["changed"] is True
    ),
    "world_route": Case(["world", "route", "hello"], seed_agent, _keys("layer", "resolved_agent_id")),
}


def _one_document(result: Any) -> Any:
    """Parse stdout as exactly one JSON document (``json.loads`` rejects trailing text)."""
    assert "\x1b[" not in result.stdout, "ANSI escape on the --json stream"
    return json.loads(result.stdout)


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_json_result_is_one_document(world: World, name: str):
    case = RESULTS[name]
    case.setup(world)
    result = runner.invoke(app, [*case.args, "--json"], env=case.env)
    assert result.exit_code == case.code, result.output
    doc = _one_document(result)
    assert case.check(doc) is not False


def test_ids_are_strings(world: World):
    seed_agent(world)
    [row] = _one_document(runner.invoke(app, ["agent", "list", "--json"]))
    assert isinstance(row["id"], str)


# ── errors ─────────────────────────────────────────────────────────────────


ERRORS: dict[str, tuple[list[str], Callable[[World], Any], int]] = {
    "status_not_initialised": (["status"], no_home, EXIT_ERROR),
    "agent_show_unknown": (["agent", "show", "ghost"], lambda _w: None, EXIT_ERROR),
    "agent_show_ambiguous": (["agent", "show", "scout"], seed_twins, EXIT_ERROR),
    "agent_create_world": (["agent", "create", "--name", "x", "--card", "world", "--model", "m"], lambda _w: None, 1),
    "agent_create_needs_a_card": (["agent", "create", "--name", "x"], lambda _w: None, EXIT_ERROR),
    "providers_show_unknown": (["providers", "show", "ghost"], lambda _w: None, EXIT_ERROR),
    "providers_login_api_key": (["providers", "login", "ollama/hermes-3"], seed_ollama, EXIT_ERROR),
    "cards_show_unknown": (["cards", "show", "nope-nope"], lambda _w: None, EXIT_ERROR),
    "cards_show_ambiguous": (["cards", "show", "the"], lambda _w: None, EXIT_ERROR),
    "mcp_show_unknown": (["mcp", "show", "ghost"], lambda _w: None, EXIT_NOT_FOUND),
    "mcp_add_reserved": (["mcp", "add", "--name", "builtin", "--url", "https://a/sse"], lambda _w: None, EXIT_ERROR),
    "mcp_add_exists": (["mcp", "add", "--name", "notion-mcp", "--url", "https://a/sse"], seed_server, EXIT_ERROR),
    "memory_list_no_target": (["memory", "list"], lambda _w: None, EXIT_ERROR),
    "memory_inspect_unknown": (
        ["memory", "inspect", "00000000-0000-0000-0000-000000000000", "--agent", "hermit"],
        seed_memory,
        EXIT_NOT_FOUND,
    ),
    "memory_list_unknown_connector": (["memory", "list", "--connector", "ghost"], lambda _w: None, EXIT_NOT_FOUND),
    "tools_subscribe_changed": (
        ["tools", "subscribe", "scout", "notion-mcp/create_page"],
        lambda w: (seed_agent(w), seed_server_with_tools(w)),
        EXIT_DENIED,
    ),
    "tools_list_unknown_agent": (["tools", "list", "--agent", "ghost"], lambda _w: None, EXIT_NOT_FOUND),
    "world_route_empty": (["world", "route", " "], lambda _w: None, EXIT_ERROR),
    "run_unknown_agent": (["run", "hello", "--agent", "ghost"], lambda _w: None, EXIT_ERROR),
}


@pytest.mark.parametrize("name", sorted(ERRORS))
def test_json_error_is_one_error_document_with_the_exit_code(world: World, name: str):
    args, setup, code = ERRORS[name]
    setup(world)
    result = runner.invoke(app, [*args, "--json"])
    assert result.exit_code == code, result.output
    doc = _one_document(result)
    assert set(doc) == {"error"}
    error = doc["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert "[/" not in error["message"] and "[bold" not in error["message"]  # plain text, no markup
    assert set(error) <= {"code", "message", "details"}


def test_json_error_details_carry_the_hints_as_plain_text(world: World):
    seed_twins(world)
    error = _one_document(runner.invoke(app, ["agent", "show", "scout", "--json"]))["error"]
    assert error["message"] == "Ambiguous name 'scout'. Use one of these IDs:"
    assert len(error["details"]) == 2 and all(len(d) == 36 for d in error["details"])


# ── secrets ────────────────────────────────────────────────────────────────

API_KEY = "sk-json-contract-key"
BEARER = "tok-json-contract-bearer"


def test_providers_show_redacts_the_key_in_both_views(world: World):
    added = runner.invoke(app, ["providers", "add", "-p", "anthropic", "-m", "claude", "-n", "work", "-k", API_KEY])
    assert added.exit_code == 0, added.output
    assert API_KEY in world.keyring.values()
    for args in (
        ["providers", "show", "work"],
        ["providers", "show", "work", "--json"],
        ["providers", "list", "--json"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert API_KEY not in result.output
    detail = _one_document(runner.invoke(app, ["providers", "show", "work", "--json"]))
    assert detail["credential"].startswith("API key in keyring")


def test_mcp_show_redacts_the_bearer_in_both_views(world: World):
    added = runner.invoke(
        app,
        ["mcp", "add", "--name", "notion-mcp", "--url", "https://a/sse", "--header", f"Authorization=Bearer {BEARER}"],
    )
    assert added.exit_code == 0, added.output
    assert BEARER not in added.output
    for args in (["mcp", "show", "notion-mcp"], ["mcp", "show", "notion-mcp", "--json"], ["mcp", "list", "--json"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert BEARER not in result.output
