"""Golden transcripts of every one-shot command's human output.

Each scenario runs a command exactly as a user at a terminal would and compares
what the terminal showed (stdout and stderr, in the order written) with a
recording. The recordings were taken from the commands before their output
went through the renderer, so a diff here is a change in what a terminal
shows. The interactive prompt sites have their own goldens
(``test_prompt_goldens.py``), as do ``cards`` and ``run``.

Set ``ARCANA_RECORD_GOLDENS=1`` to (re)record the files instead of comparing.
"""

import os
import re
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from arcana.agents.registry import AgentRegistry
from arcana.types.card import Card
from arcana.types.model import ModelProvider
from arcana_cli.main import app
from tests.support.world import (
    MEMORY_ID,
    World,
    no_home,
    seed_agent,
    seed_connection,
    seed_memory,
    seed_ollama,
    seed_server,
    seed_server_with_tools,
    seed_soul,
    seed_twins,
    seed_unset_model,
)

runner = CliRunner()

GOLDEN = Path(__file__).parent / "golden" / "output"
GOLDEN_ENV: dict[str, str | None] = {"COLUMNS": "100", "FORCE_COLOR": None, "TTY_COMPATIBLE": None}
RECORD = os.environ.get("ARCANA_RECORD_GOLDENS") == "1"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHORT_ID = re.compile(r"\b[0-9a-f]{8}…")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\+00:00| UTC)?)?")
_LATENCY = re.compile(r"\b\d+ ms *")


def normalised(output: str, w: World) -> str:
    """Ids, dates, latencies and the temp home are fresh per run; replace them so the golden is stable."""
    output = output.replace(str(w.root.parent), "<tmp>")
    output = _UUID.sub("<id>", output)
    output = _SHORT_ID.sub("<short>…", output)
    output = _DATE.sub("<date>", output)
    return _LATENCY.sub("<n> ms ", output)


# ── seeds ──────────────────────────────────────────────────────────────────


def seed_notes(w: World) -> None:
    notes = w.root.parent / "notes"
    notes.mkdir()
    (notes / "tea.md").write_text("# Tea\n\nOolong is best brewed at 90C.\n", encoding="utf-8")


def seed_home(w: World) -> None:
    """A ``~/.arcana`` that already exists (``install_world`` created one), holding an agent and a connection."""
    seed_ollama(w)
    seed_agent(w)


# ── the scenarios ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scenario:
    args: list[str]
    setup: Callable[[World], None] = lambda _w: None
    patches: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str | None] = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    # status / init
    "status": Scenario(["status"], seed_home, env={"COLUMNS": "300"}),
    "status_not_initialised": Scenario(["status"], no_home),
    "init": Scenario(["init"], no_home, env={"COLUMNS": "300"}),
    "init_exists": Scenario(["init"]),
    # agent
    "agent_list_empty": Scenario(["agent", "list"]),
    "agent_list": Scenario(["agent", "list"], lambda w: (seed_agent(w), seed_unset_model(w))),
    "agent_show": Scenario(["agent", "show", "scout"], seed_agent),
    "agent_show_unset_model": Scenario(["agent", "show", "drifter"], seed_unset_model),
    "agent_show_unknown": Scenario(["agent", "show", "ghost"]),
    "agent_show_unknown_id": Scenario(["agent", "show", "00000000-0000-0000-0000-000000000000"]),
    "agent_show_ambiguous": Scenario(["agent", "show", "scout"], seed_twins),
    "agent_create_world": Scenario(["agent", "create", "--name", "x", "--card", "world", "--model", "ollama"]),
    "agent_create_bad_model": Scenario(["agent", "create", "--name", "x", "--card", "hermit", "--model", "a b"]),
    "agent_create_flags": Scenario(
        ["agent", "create", "--name", "scout", "--card", "hermit", "--model", "ollama/hermes-3"]
    ),
    "agent_edit_flags": Scenario(
        [
            "agent",
            "edit",
            "scout",
            "--name",
            "ranger",
            "--description",
            "d",
            "--card",
            "fool",
            "--model",
            "ollama/x",
            "--tags",
            "a,b",
        ],
        seed_agent,
    ),
    "agent_delete_yes_flag": Scenario(["agent", "delete", "scout", "--yes"], seed_agent),
    # providers
    "providers_list_empty": Scenario(["providers", "list"]),
    "providers_list": Scenario(["providers", "list"], seed_ollama),
    "providers_show": Scenario(["providers", "show", "ollama/hermes-3"], seed_ollama),
    "providers_show_unknown": Scenario(["providers", "show", "ghost"]),
    "providers_login_api_key": Scenario(["providers", "login", "ollama/hermes-3"], seed_ollama),
    "providers_edit_flags": Scenario(
        ["providers", "edit", "ollama/hermes-3", "--base-url", "http://gpu:11434", "--no-verify"], seed_ollama
    ),
    "providers_edit_header_not_custom": Scenario(
        ["providers", "edit", "ollama/hermes-3", "--header", "X-A: b", "--no-verify"], seed_ollama
    ),
    "providers_edit_no_default_model": Scenario(
        ["providers", "edit", "bare", "--base-url", "http://x"],
        lambda w: seed_connection(w, "bare", ModelProvider.OLLAMA, ""),
    ),
    "providers_remove_dependents": Scenario(
        ["providers", "remove", "ollama/hermes-3", "--yes"], lambda w: (seed_ollama(w), seed_agent(w))
    ),
    "providers_remove_yes_flag": Scenario(["providers", "remove", "ollama/hermes-3", "--yes"], seed_ollama),
    "providers_add_flags": Scenario(
        ["providers", "add", "-p", "ollama", "-m", "hermes-3", "-n", "local", "-e", "http://localhost:11434"]
    ),
    "providers_add_oauth_keyless": Scenario(["providers", "add", "-p", "ollama", "--oauth"]),
    # mcp
    "mcp_list_empty": Scenario(["mcp", "list"]),
    "mcp_list": Scenario(["mcp", "list"], seed_server_with_tools),
    "mcp_show": Scenario(["mcp", "show", "notion-mcp"], seed_server_with_tools),
    "mcp_show_no_tools": Scenario(["mcp", "show", "notion-mcp"], seed_server),
    "mcp_show_unknown": Scenario(["mcp", "show", "ghost"]),
    "mcp_refresh": Scenario(["mcp", "refresh", "notion-mcp"], seed_server_with_tools),
    "mcp_add": Scenario(["mcp", "add", "--name", "notion-mcp", "--url", "https://a/sse"]),
    "mcp_add_exists": Scenario(["mcp", "add", "--name", "notion-mcp", "--url", "https://a/sse"], seed_server),
    "mcp_add_reserved": Scenario(["mcp", "add", "--name", "builtin", "--url", "https://a/sse"]),
    "mcp_add_url_and_command": Scenario(["mcp", "add", "--name", "x", "--url", "https://a/sse", "--command", "c"]),
    "mcp_approve_all": Scenario(["mcp", "approve", "notion-mcp", "--all"], seed_server_with_tools),
    "mcp_approve_nothing": Scenario(
        ["mcp", "approve", "notion-mcp", "--tool", "search_pages"], seed_server_with_tools
    ),
    "mcp_approve_unknown_tool": Scenario(["mcp", "approve", "notion-mcp", "--tool", "ghost"], seed_server_with_tools),
    "mcp_login_not_oauth": Scenario(["mcp", "login", "notion-mcp"], seed_server),
    "mcp_remove_dependents": Scenario(
        ["mcp", "remove", "notion-mcp", "--yes"],
        lambda w: (seed_server(w), seed_agent(w, subs=["notion-mcp/search_pages"])),
    ),
    "mcp_remove_force": Scenario(
        ["mcp", "remove", "notion-mcp", "--yes", "--force"],
        lambda w: (seed_server(w), seed_agent(w, subs=["notion-mcp/search_pages"])),
    ),
    # memory
    "memory_list": Scenario(["memory", "list", "--agent", "hermit"], seed_memory),
    "memory_list_empty_pool": Scenario(["memory", "list", "--agent", "hermit", "--pool", "team"], seed_memory),
    "memory_list_no_target": Scenario(["memory", "list"]),
    "memory_list_connector_and_agent": Scenario(["memory", "list", "--connector", "n", "--agent", "a"]),
    "memory_list_unknown_connector": Scenario(["memory", "list", "--connector", "ghost"]),
    "memory_list_unknown_agent": Scenario(["memory", "list", "--agent", "ghost"]),
    "memory_search": Scenario(["memory", "search", "oolong", "--agent", "hermit"], seed_memory),
    "memory_inspect": Scenario(["memory", "inspect", str(MEMORY_ID), "--agent", "hermit"], seed_memory),
    "memory_inspect_bad_id": Scenario(["memory", "inspect", "nope", "--agent", "hermit"], seed_memory),
    "memory_inspect_unknown": Scenario(
        ["memory", "inspect", "00000000-0000-0000-0000-000000000000", "--agent", "hermit"], seed_memory
    ),
    "memory_forget_archive": Scenario(
        ["memory", "forget", str(MEMORY_ID), "--agent", "hermit", "--archive", "--yes"], seed_memory
    ),
    "memory_adapters_empty": Scenario(["memory", "adapters"]),
    "memory_connect_markdown": Scenario(
        ["memory", "connect", "markdown", "--path", "<tmp>/notes", "--name", "notes"],
        seed_notes,
        env={"COLUMNS": "300"},
    ),
    "memory_adapters": Scenario(
        ["memory", "adapters"],
        lambda w: (seed_notes(w), _connect_notes(w)),
        env={"COLUMNS": "300"},
    ),
    "memory_list_connector": Scenario(
        ["memory", "list", "--connector", "notes"], lambda w: (seed_notes(w), _connect_notes(w))
    ),
    "memory_export": Scenario(["memory", "export", "--agent", "hermit"], seed_memory),
    "memory_export_out": Scenario(
        ["memory", "export", "--agent", "hermit", "--out", "<tmp>/export.md"], seed_memory, env={"COLUMNS": "300"}
    ),
    "memory_export_none_chosen": Scenario(["memory", "export"]),
    # tools
    "tools_list": Scenario(["tools", "list"]),
    "tools_list_agent": Scenario(
        ["tools", "list", "--agent", "scout"], lambda w: seed_agent(w, subs=["builtin/web_search"])
    ),
    "tools_list_unknown_agent": Scenario(["tools", "list", "--agent", "ghost"]),
    "tools_subscribe": Scenario(["tools", "subscribe", "scout", "builtin/web_search"], seed_agent),
    "tools_subscribe_already": Scenario(
        ["tools", "subscribe", "scout", "builtin/web_search"], lambda w: seed_agent(w, subs=["builtin/web_search"])
    ),
    "tools_subscribe_unknown": Scenario(["tools", "subscribe", "scout", "nope/tool"], seed_agent),
    "tools_subscribe_changed": Scenario(
        ["tools", "subscribe", "scout", "notion-mcp/create_page"], lambda w: (seed_agent(w), seed_server_with_tools(w))
    ),
    "tools_subscribe_server": Scenario(
        ["tools", "subscribe", "scout", "notion-mcp"], lambda w: (seed_agent(w), seed_server(w))
    ),
    "tools_subscribe_unknown_server": Scenario(["tools", "subscribe", "scout", "ghost/*"], seed_agent),
    "tools_unsubscribe_not_subscribed": Scenario(["tools", "unsubscribe", "scout", "builtin/web_search"], seed_agent),
    "tools_unsubscribe_ambiguous": Scenario(["tools", "unsubscribe", "scout", "builtin/web_search"], seed_twins),
    # world
    "world_route": Scenario(["world", "route", "hello"], seed_agent),
    "world_route_empty": Scenario(["world", "route", "  "]),
    "world_route_unknown_agent": Scenario(["world", "route", "hello", "--agent", "ghost"], seed_agent),
    "world_route_ambiguous_agent": Scenario(["world", "route", "hello", "--agent", "scout"], seed_twins),
    "world_route_no_route": Scenario(
        ["world", "route", "hello"],
        lambda w: (AgentRegistry(w.agents).create(name="one", card=Card.FOOL, model="m"), seed_agent(w)),
    ),
    # soul
    "soul_show_none": Scenario(["soul", "show"]),
    "soul_show": Scenario(["soul", "show"], seed_soul),
    "soul_edit_not_initialised": Scenario(["soul", "edit"], no_home),
    # chat (what fails before the session opens)
    "chat_unknown_agent": Scenario(["chat", "--agent", "ghost"]),
    "chat_ambiguous_agent": Scenario(["chat", "--agent", "scout"], seed_twins),
    "chat_no_model": Scenario(["chat", "--agent", "drifter"], seed_unset_model),
    "chat_bad_session": Scenario(["chat", "--agent", "scout", "--session", "nope"], seed_agent),
}


def _connect_notes(w: World) -> None:
    result = runner.invoke(
        app, ["memory", "connect", "markdown", "--path", str(w.root.parent / "notes"), "--name", "notes"]
    )
    assert result.exit_code == 0, result.output


def transcript(w: World, scenario: Scenario) -> str:
    """Run ``scenario`` in ``w`` and return ``exit=<code>`` plus what the terminal showed (stdout and stderr)."""
    scenario.setup(w)
    args = [a.replace("<tmp>", str(w.root.parent)) for a in scenario.args]
    with ExitStack() as stack:
        for target, value in scenario.patches.items():
            stack.enter_context(patch(target, return_value=value))
        result = runner.invoke(app, args, env={**GOLDEN_ENV, **scenario.env})
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return f"exit={result.exit_code}\n{normalised(result.output, w)}"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_terminal_output_matches_golden(world: World, name: str):
    got = transcript(world, SCENARIOS[name])
    path = GOLDEN / f"{name}.txt"
    if RECORD:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
    assert got == path.read_text()
