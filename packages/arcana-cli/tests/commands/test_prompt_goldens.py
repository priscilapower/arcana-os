"""Golden transcripts of every interactive prompt site, driven through ``CliRunner`` input.

Each scenario runs a wizard or a destructive confirm exactly as a user answering
at a line prompt would, and compares the transcript (typed answers included;
hidden answers are never echoed) with a recording, so a diff here is a change
in what a terminal shows. The scenarios also pin the fail-closed paths: piped
input that runs out before a question, and ``--json`` never prompting.

Set ``ARCANA_RECORD_GOLDENS=1`` to (re)record the files instead of comparing.
"""

import logging
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

from arcana.types.card import Card
from arcana.types.model import ModelProvider
from arcana_cli.main import app
from tests.support.world import (
    MEMORY_ID,
    World,
    seed_agent,
    seed_connection,
    seed_memory,
    seed_ollama,
    seed_server,
)

runner = CliRunner()

GOLDEN = Path(__file__).parent / "golden" / "prompts"
# Pin the console width and keep colour off so the recorded output is stable.
GOLDEN_ENV: dict[str, str | None] = {"COLUMNS": "100", "FORCE_COLOR": None, "TTY_COMPATIBLE": None}
RECORD = os.environ.get("ARCANA_RECORD_GOLDENS") == "1"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

#: The secrets typed at hidden prompts; a transcript must never contain one.
API_KEY = "sk-golden-api-key"
BEARER = "tok-golden-bearer"


def normalised(output: str) -> str:
    """Record ids are fresh per run; replace them so the golden is stable."""
    return _UUID.sub("<id>", output)


# ── the scenarios ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scenario:
    args: list[str]
    input: str
    setup: Callable[[World], None] = lambda _w: None
    patches: dict[str, Any] = field(default_factory=dict)


SCENARIOS: dict[str, Scenario] = {
    "agent_create": Scenario(
        ["agent", "create"],
        "scout\nn\n1\n\n",
        seed_ollama,
        {"arcana_cli.commands.agent.select_card": Card.HERMIT},
    ),
    "agent_create_blend": Scenario(
        ["agent", "create"],
        "scout\ny\n1\nhermes-3:8b\n",
        seed_ollama,
        {
            "arcana_cli.commands.agent.select_card": Card.HERMIT,
            "arcana_cli.commands.agent.select_cards": [Card.TOWER],
        },
    ),
    "agent_create_other_reference": Scenario(
        ["agent", "create"],
        "scout\nn\n2\nanthropic lol\nanthropic/claude-sonnet-4-6\n",
        seed_ollama,
        {"arcana_cli.commands.agent.select_card": Card.HERMIT},
    ),
    "agent_create_piped_input_ran_out": Scenario(["agent", "create"], ""),
    "agent_create_no_connections": Scenario(
        ["agent", "create", "--card", "hermit"],
        "scout\n",
    ),
    "agent_edit": Scenario(
        ["agent", "edit", "scout"],
        "ranger\nA quiet scout\nn\n\nfield, research\n",
        lambda w: (seed_ollama(w), seed_agent(w)),
        {"arcana_cli.commands.agent.select_card": Card.HERMIT},
    ),
    "agent_delete_yes": Scenario(["agent", "delete", "scout"], "y\n", seed_agent),
    "agent_delete_no": Scenario(["agent", "delete", "scout"], "n\n", seed_agent),
    "agent_delete_piped_input_ran_out": Scenario(["agent", "delete", "scout"], "", seed_agent),
    "providers_add_ollama": Scenario(["providers", "add"], "ollama\nhermes-3\n\n\n"),
    "providers_add_unknown_provider": Scenario(["providers", "add"], "claude\nOllama\nhermes-3\n\n\n"),
    "providers_add_piped_input_ran_out": Scenario(["providers", "add", "--provider", "anthropic"], "claude\n"),
    "providers_add_anthropic": Scenario(["providers", "add"], f"anthropic\nclaude-sonnet-4-6\nwork\n{API_KEY}\n"),
    "providers_add_overwrite_yes_flag": Scenario(
        [
            "providers",
            "add",
            "-p",
            "ollama",
            "-m",
            "hermes-3",
            "-n",
            "ollama/hermes-3",
            "-e",
            "http://gpu:11434",
            "--yes",
        ],
        "",
        seed_ollama,
    ),
    "providers_add_overwrite_no": Scenario(
        ["providers", "add", "--provider", "ollama", "--model-id", "hermes-3", "--name", "ollama/hermes-3"],
        "\nn\n",
        seed_ollama,
    ),
    "providers_edit": Scenario(
        ["providers", "edit", "work", "--no-verify"],
        f"https://proxy.example/v1\ny\n{API_KEY}\n",
        lambda w: seed_connection(w, "work", ModelProvider.OPENAI, "gpt-4"),
    ),
    "providers_edit_keep": Scenario(
        ["providers", "edit", "work", "--no-verify"],
        "\n\n",
        lambda w: seed_connection(w, "work", ModelProvider.OPENAI, "gpt-4", "https://api.openai.com/v1"),
    ),
    "providers_edit_rotate_key": Scenario(
        ["providers", "edit", "work", "--rotate-key", "--no-verify"],
        f"{API_KEY}\n",
        lambda w: seed_connection(w, "work", ModelProvider.OPENAI, "gpt-4"),
    ),
    "providers_remove_yes": Scenario(["providers", "remove", "ollama/hermes-3"], "y\n", seed_ollama),
    "providers_remove_no": Scenario(["providers", "remove", "ollama/hermes-3"], "n\n", seed_ollama),
    "mcp_add_bearer": Scenario(
        ["mcp", "add", "--name", "notion-mcp", "--url", "https://mcp.notion.com/sse", "--header", "Authorization="],
        f"{BEARER}\n",
    ),
    "mcp_add_json_blank_bearer": Scenario(
        ["mcp", "add", "--name", "notion-mcp", "--url", "https://a/sse", "--header", "Authorization=", "--json"],
        f"{BEARER}\n",
    ),
    "mcp_remove_json_without_yes": Scenario(["mcp", "remove", "notion-mcp", "--json"], "y\n", seed_server),
    "mcp_remove_yes": Scenario(["mcp", "remove", "notion-mcp"], "y\n", seed_server),
    "mcp_remove_no": Scenario(["mcp", "remove", "notion-mcp"], "n\n", seed_server),
    "memory_forget_yes": Scenario(["memory", "forget", str(MEMORY_ID), "--agent", "hermit"], "y\n", seed_memory),
    "memory_forget_json_without_yes": Scenario(
        ["memory", "forget", str(MEMORY_ID), "--agent", "hermit", "--json"], "y\n", seed_memory
    ),
    "memory_forget_no": Scenario(["memory", "forget", str(MEMORY_ID), "--agent", "hermit"], "n\n", seed_memory),
    "tools_unsubscribe_yes": Scenario(
        ["tools", "unsubscribe", "scout", "builtin/web_search"],
        "y\n",
        lambda w: seed_agent(w, subs=["builtin/web_search"]),
    ),
    "tools_unsubscribe_json_without_yes": Scenario(
        ["tools", "unsubscribe", "scout", "builtin/web_search", "--json"],
        "y\n",
        lambda w: seed_agent(w, subs=["builtin/web_search"]),
    ),
    "tools_unsubscribe_no": Scenario(
        ["tools", "unsubscribe", "scout", "builtin/web_search"],
        "n\n",
        lambda w: seed_agent(w, subs=["builtin/web_search"]),
    ),
}


def transcript(w: World, scenario: Scenario) -> str:
    """Run ``scenario`` in ``w`` and return ``exit=<code>`` plus what the terminal showed (stdout and stderr)."""
    scenario.setup(w)
    with ExitStack() as stack:
        for target, value in scenario.patches.items():
            stack.enter_context(patch(target, return_value=value))
        result = runner.invoke(app, scenario.args, input=scenario.input, env=GOLDEN_ENV)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return f"exit={result.exit_code}\n{normalised(result.output)}"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_prompt_transcript_matches_golden(world: World, name: str):
    got = transcript(world, SCENARIOS[name])
    if RECORD:
        (GOLDEN / f"{name}.txt").write_text(got)
    assert got == (GOLDEN / f"{name}.txt").read_text()


SECRET_SCENARIOS = sorted(n for n, s in SCENARIOS.items() if API_KEY in s.input or BEARER in s.input)


@pytest.mark.parametrize("name", SECRET_SCENARIOS)
def test_a_secret_typed_at_a_hidden_prompt_never_reaches_the_transcript_or_the_log(
    world: World, name: str, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    scenario = SCENARIOS[name]
    secret = API_KEY if API_KEY in scenario.input else BEARER
    got = transcript(world, scenario)
    assert secret not in got
    assert secret not in caplog.text
    if got.startswith("exit=0"):
        assert secret in world.keyring.values()  # it reached the keyring, and only the keyring
    else:
        assert secret not in world.keyring.values()  # refused before it was ever read


def test_every_secret_prompt_site_is_covered():
    # API key on add, on interactive edit, on --rotate-key; the MCP bearer token; and --json refusing one.
    assert SECRET_SCENARIOS == [
        "mcp_add_bearer",
        "mcp_add_json_blank_bearer",
        "providers_add_anthropic",
        "providers_edit",
        "providers_edit_rotate_key",
    ]
