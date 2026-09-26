"""The ``arcana`` commands inside the session: ``/agent``, ``/providers``, ``/mcp`` and the rest.

Each slash command runs the one-shot command's own body, so these tests check
the session's side of it: arguments parse with the one-shot options, a missing
required argument is asked for, a command's failure is a note (never the end of
the session), a secret can't ride on the command line, the session picks up
what a command changed, and an MCP server added in the session stays out of the
running agent's tools until it is approved. The controller runs over a
:class:`RecordingRenderer`; the dialogs themselves are covered by
``test_chat_slash_commands_app.py``, the registry by ``tests/tui/test_slash_registry.py``.
"""

import asyncio
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.mcp as mcp_mod
from arcana.agents.registry import AgentRegistry
from arcana.models import ConnectionStore
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from arcana_cli.commands.chat.controller import SECRET_ON_THE_LINE, _ChatController, _keeps_in_history
from arcana_cli.main import app
from arcana_cli.tui.slash_registry import HIDDEN, SlashCommand, SlashUsageError, slash_registry
from arcana_cli.ui.input_model import SessionCommandName
from tests.support.chat import (
    FakeGateway,
    create_agent,
    make_controller,
    mock_runtime,
    patch_body,
    patch_build,
    use_arcana_home,
)
from tests.support.renderer import RecordingRenderer
from tests.support.world import FakeMCPAdapter, World, install_world, seed_server

runner = CliRunner()

API_KEY = "sk-in-session-secret"
BEARER = "tok-in-session-bearer"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """One scratch ``ARCANA_HOME`` the chat modules and every command group share, keyring included."""
    use_arcana_home(tmp_path, monkeypatch)
    return install_world(tmp_path, monkeypatch)


def _controller(home: World, **kwargs: Any) -> _ChatController:
    record = create_agent(home.root)
    return make_controller(home.root, record, mock_runtime(), **kwargs)


def _out(c: _ChatController) -> str:
    assert isinstance(c.renderer, RecordingRenderer)
    return c.renderer.text()


def _notes(c: _ChatController) -> str:
    assert isinstance(c.renderer, RecordingRenderer)
    return c.renderer.notes_text()


def _errors(c: _ChatController) -> str:
    assert isinstance(c.renderer, RecordingRenderer)
    return c.renderer.errors_text()


def _registry_names(registry: MCPRegistry) -> set[str]:
    return {server.name for server in registry.list_servers()}


def _command(line: str) -> tuple[SlashCommand, str]:
    found = slash_registry().resolve(line)
    assert found.command is not None, line
    return found.command, found.args


# ── parsing ───────────────────────────────────────────────────────────────


async def test_arguments_parse_with_the_one_shot_commands_options():
    command, args = _command("/providers add --provider ollama --scope a --scope b -y")
    assert command.name == "/providers add"
    params = await command.parse(RecordingRenderer(), args)
    assert (params["provider"], params["scope"], params["yes"]) == ("ollama", ["a", "b"], True)


async def test_a_repeatable_option_left_off_arrives_as_the_one_shot_command_sees_it():
    command, args = _command("/mcp approve notion-mcp --all")
    params = await command.parse(RecordingRenderer(), args)
    assert params == {"name": "notion-mcp", "tool": None, "all_": True}


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("/providers add --bogus", "No such option: --bogus"),
        ("/providers add --help", "No such option: --help"),
        ("/agent create --name 'unbalanced", "No closing quotation"),
        ("/memory list --scope everywhere", "'everywhere' is not one of"),
        ("/mcp remove notion-mcp --json", "--json isn't available inside the session"),
        ("/agent delete scout --json", "--json isn't available inside the session"),
        ("/providers remove ollama --json", "--json isn't available inside the session"),
        ("/cards --json", "--json isn't available inside the session"),
    ],
)
async def test_arguments_that_dont_parse_say_why_and_print_nothing(line, message, capsys):
    command, args = _command(line)
    with pytest.raises(SlashUsageError, match=message):
        await command.parse(RecordingRenderer(), args)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("line", "asked", "param", "answer"),
    [
        ("/mcp add --url https://a/sse", "Server name", "name", "notion-mcp"),
        ("/agent delete --yes", "Agent name or UUID", "name", "scout"),
        ("/memory inspect 1234", "Agent name or UUID", "agent", "scout"),
    ],
)
async def test_a_required_argument_left_off_is_asked_for(line, asked, param, answer):
    command, args = _command(line)
    r = RecordingRenderer(answers=[answer])
    params = await command.parse(r, args)
    assert params[param] == answer
    assert [q.prompt for q in r.questions][0].startswith(asked)


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        (f"/providers add --api-key {API_KEY}", f"/providers add --api-key {HIDDEN}"),
        (f"/providers add --api-key={API_KEY} -y", f"/providers add --api-key={HIDDEN} -y"),
        (f"/providers add -k{API_KEY}", f"/providers add -k{HIDDEN}"),
        (f"/providers add -yk {API_KEY} --provider anthropic", f"/providers add -yk {HIDDEN} --provider anthropic"),
        (f"/mcp add --name n --header 'Authorization=Bearer {BEARER}'", f"/mcp add --name n --header {HIDDEN}"),
        (f"/mcp add --name n --header Authorization={BEARER}", f"/mcp add --name n --header {HIDDEN}"),
        (f"/providers add --api-key '{API_KEY}", f"/providers add --api-key {HIDDEN}"),  # unbalanced quote
        (f"/providers  add --api-key {API_KEY}", f"/providers add --api-key {HIDDEN}"),  # extra space
        (f"/providers ad --api-key {API_KEY}", f"/providers ad --api-key {HIDDEN}"),  # mistyped action
        (f"/provider add --api-key {API_KEY}", f"/provider add --api-key {HIDDEN}"),  # mistyped group
        (f"/agent create --api-key={API_KEY}", f"/agent create --api-key={HIDDEN}"),  # the wrong command
        (f"/agent create -k {API_KEY}", f"/agent create -k {HIDDEN}"),  # the short spelling, on the wrong command
        (f"/providers ad -k{API_KEY}", f"/providers ad -k{HIDDEN}"),  # and on a mistyped action
        (f"/nonsense -yk {API_KEY}", f"/nonsense -yk {HIDDEN}"),  # and on a line that names nothing
    ],
)
def test_a_secret_on_the_line_is_redacted(line, shown):
    assert slash_registry().redacted(line) == shown
    assert not _keeps_in_history(line)


@pytest.mark.parametrize(
    "line",
    [
        "/providers add --api-key-env MY_KEY",
        "/mcp add --name n --url https://a/sse --header Authorization=",
        "/mcp add --name n --url https://a/sse --header 'Authorization=Bearer '",
        "/providers edit anthropic --rotate-key",
        f"/switch {API_KEY}",
        "/providers edit custom --header 'Authorization: Bearer x'",
        f"tell me about --api-key {API_KEY}",
    ],
)
def test_a_line_without_a_secret_option_is_left_alone(line):
    assert slash_registry().redacted(line) is None
    assert _keeps_in_history(line)


# ── dispatch and failures ─────────────────────────────────────────────────


@pytest.mark.parametrize("line", ["/agent", "/providers frobnicate", "/mcp approve-all", "/soul"])
async def test_a_missing_or_unknown_action_shows_the_usage(home, line):
    c = _controller(home)
    await c.submit(line)
    group = line.split(" ")[0]
    assert f"Usage: {group} {'|'.join(slash_registry().groups[group])}" in _out(c)
    assert not c.exited


def test_every_session_command_has_a_handler(home):
    c = _controller(home)
    assert set(c._session_commands) == set(SessionCommandName)  # pyright: ignore[reportPrivateUsage]


async def test_an_unknown_command_says_so(home):
    c = _controller(home)
    await c.submit("/frobnicate now")
    assert "Unknown command /frobnicate. Type /help." in _out(c)


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("/soul edit", "$EDITOR"),
        ("/run hello", "the session is the conversation"),
        ("/chat", "the session you are already in"),
        ("/init", "nothing left to initialise"),
    ],
)
async def test_a_command_kept_out_of_the_session_says_why(home, line, why):
    c = _controller(home)
    await c.submit(line)
    out = _out(c)
    assert "isn't available in the session" in out and why in out
    assert f"arcana {line.removeprefix('/').split(' ')[0]}" in out
    assert not c.exited


async def test_shell_syntax_on_a_slash_line_never_reaches_a_shell(home, monkeypatch):
    def no_shell(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a slash line reached a shell")

    for module, name in [
        (subprocess, "Popen"),
        (subprocess, "run"),
        (os, "system"),
        (asyncio, "create_subprocess_shell"),
        (asyncio, "create_subprocess_exec"),
    ]:
        monkeypatch.setattr(module, name, no_shell)
    c = _controller(home)
    await c.submit("/mcp add x; rm -rf ~")
    await c.submit("/agent show scout; rm -rf ~")
    assert "No such option" in _out(c) or "unexpected extra argument" in _out(c)
    assert not c.exited


async def test_a_usage_error_points_at_the_commands_help(home):
    c = _controller(home)
    await c.submit("/mcp add --bogus")
    out = _out(c)
    assert "/mcp add: No such option: --bogus" in out
    assert "/mcp add --help" in out


async def test_help_on_a_command_lists_its_session_options(home):
    c = _controller(home)
    await c.submit("/providers add --help")
    out = _out(c)
    assert "/providers add [options]" in out
    assert "--provider" in out and "--api-key-env" in out
    assert "--api-key," not in out and "--json" not in out  # the secret and the surface-only options are hidden
    assert not ConnectionStore(home.models).get_by_name("work")


async def test_help_lists_the_session_commands_and_every_command_group(home):
    c = _controller(home)
    await c.submit("/help")
    out = _out(c)
    assert "In-session commands" in out
    for name in (
        "/switch",
        "/fresh",
        "/exit",
        "/agent",
        "/providers",
        "/mcp",
        "/memory",
        "/tools",
        "/world",
        "/status",
    ):
        assert name in out
    assert "list · search · inspect" in out
    assert "--help" in out


async def test_memory_alone_is_the_session_view_and_with_an_action_the_command(home):
    c = _controller(home)
    await c.submit("/memory")
    assert "Usage: /memory" not in _out(c)
    await c.submit("/memory adapters")
    assert "No knowledge connectors" in _out(c) or "connector" in _out(c).lower()


async def test_a_read_only_command_runs_in_the_session(home):
    c = _controller(home)
    await c.submit("/agent list")
    assert "scout" in _out(c)
    await c.submit("/status")
    assert "Agents" in _out(c)


async def test_a_command_that_exits_with_an_error_leaves_the_session_running(home):
    c = _controller(home)
    await c.submit("/agent delete ghost")
    assert "No agent 'ghost'." in _errors(c)
    assert not c.exited
    await c.submit("/help")  # still taking commands
    assert "In-session commands" in _out(c)


async def test_a_command_that_raises_becomes_an_error_note(home, monkeypatch):
    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk on fire")

    patch_body(monkeypatch, "/agent delete", boom)
    c = _controller(home)
    await c.submit("/agent delete scout --yes")
    assert "/agent delete failed: disk on fire" in _out(c)
    assert not c.exited


async def test_a_declined_confirmation_is_cancelled_quietly(home):
    c = _controller(home, renderer=RecordingRenderer(confirms=[False]))
    await c.submit("/agent delete scout")
    assert "Cancelled." in _notes(c)
    assert AgentRegistry(home.agents).list()


async def test_a_secret_on_the_line_is_refused_and_never_shown(home):
    c = _controller(home)
    await c.submit(f"/providers add --provider anthropic --model-id m --name work --api-key {API_KEY}")
    out = _out(c)
    assert API_KEY not in out
    assert f"--api-key {HIDDEN}" in out
    assert SECRET_ON_THE_LINE.split(":")[0] in out
    assert ConnectionStore(home.models).get_by_name("work") is None
    assert home.keyring == {}


async def test_mcp_add_cancelled_during_discovery_leaves_no_credential(home, monkeypatch):
    async def cancelled(_self: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(FakeMCPAdapter, "discover", cancelled)
    r = RecordingRenderer(answers=[BEARER])
    with pytest.raises(asyncio.CancelledError):
        await mcp_mod.add_server(
            r,
            name="notion-mcp",
            url="https://a/sse",
            command=None,
            args=[],
            transport=None,
            header=["Authorization="],
            auth_key=None,
            oauth=False,
            issuer=None,
            scope=[],
            device=False,
            description="",
        )
    assert home.keyring == {}
    assert not home.mcps.exists() or "notion-mcp" not in home.mcps.read_text()


# ── the session picks up what a command changed ───────────────────────────


async def test_an_agent_created_in_the_session_can_be_switched_to_at_once(home, monkeypatch):
    patch_build(monkeypatch, mock_runtime())
    c = _controller(home)
    await c.submit("/agent create --name oracle --card priestess --model ollama/hermes-3")
    assert "Agent 'oracle' created." in _out(c)
    await c.submit("/switch oracle")
    assert c.record.name == "oracle"


async def test_editing_the_current_agent_rebuilds_its_runtime(home, monkeypatch):
    calls = patch_build(monkeypatch, mock_runtime())
    c = _controller(home)
    await c.submit("/agent edit scout --name scout --description d --card tower --model ollama/hermes-3 --tags x")
    assert c.record.card is Card.TOWER
    assert len(calls) == 1
    assert "(reloaded scout)" in _out(c)


async def test_editing_another_agent_leaves_the_runtime_alone(home, monkeypatch):
    calls = patch_build(monkeypatch, mock_runtime())
    c = _controller(home)
    create_agent(home.root, name="oracle")
    await c.submit("/agent edit oracle --name oracle --description d --card tower --model ollama/hermes-3 --tags x")
    assert c.record.card is Card.HERMIT
    assert calls == []


async def test_deleting_the_current_agent_says_the_session_runs_on(home):
    c = _controller(home)
    await c.submit("/agent delete scout --yes")
    assert "'scout' was deleted; this session runs on until you /switch." in _out(c)
    assert not c.exited


async def test_a_providers_command_refreshes_the_sessions_connections(home):
    c = _controller(home)
    gateway = c._gw  # pyright: ignore[reportPrivateUsage]
    assert isinstance(gateway, FakeGateway)
    before = c._connections.all()  # pyright: ignore[reportPrivateUsage]  (loads the cache)
    await c.submit("/providers add --provider ollama --model-id llama3 --name local --endpoint http://localhost:11434")
    after = c._connections.all()  # pyright: ignore[reportPrivateUsage]
    assert {conn.name for conn in after} - {conn.name for conn in before} == {"local"}
    assert gateway.closed  # cached adapters dropped, so the next call reconnects


async def test_switch_to_an_ambiguous_name_lists_the_ids_without_leaving_the_session(home, capsys):
    c = _controller(home)
    first = create_agent(home.root, name="twin")
    second = create_agent(home.root, name="twin")
    await c.submit("/switch twin")
    out = _out(c)
    assert "Ambiguous agent name 'twin'" in out
    assert str(first.id) in out and str(second.id) in out
    assert c.record.name == "scout"
    assert not c.exited
    assert capsys.readouterr().out == ""


# ── MCP servers added in the session wait for approval ────────────────────


async def test_a_server_added_in_the_session_stays_out_of_its_tools_until_approved(home, monkeypatch):
    calls = patch_build(monkeypatch, mock_runtime())
    c = _controller(home)
    await c.submit("/mcp add --name notion-mcp --url https://mcp.notion.com/sse")
    assert "Connected 'notion-mcp'" in _out(c)
    assert c.unapproved == {"notion-mcp"}
    assert "notion-mcp" not in _registry_names(c.tools)
    assert calls == []  # nothing re-resolved on add
    assert "until you approve it: /mcp approve notion-mcp --all" in _out(c)

    await c.submit("/fresh")  # a rebuild for another reason keeps it out
    assert "notion-mcp" not in _registry_names(calls[-1]["tool_registry"])

    await c.submit("/mcp approve notion-mcp --all")
    assert c.unapproved == set()
    assert "notion-mcp" in _registry_names(c.tools)
    assert "notion-mcp" in _registry_names(calls[-1]["tool_registry"])
    assert "'notion-mcp' is now in this session's tools." in _out(c)


async def test_approving_one_server_keeps_another_unapproved_one_out(home, monkeypatch):
    patch_build(monkeypatch, mock_runtime())
    c = _controller(home)
    await c.submit("/mcp add --name first --url https://a/sse")
    await c.submit("/mcp add --name second --url https://b/sse")
    await c.submit("/mcp approve first --all")
    assert _registry_names(c.tools) >= {"first"}
    assert "second" not in _registry_names(c.tools)
    assert c.unapproved == {"second"}


async def test_a_failed_add_of_an_existing_server_doesnt_hold_it_back(home, monkeypatch):
    patch_build(monkeypatch, mock_runtime())
    seed_server(home)
    c = _controller(home)
    await c.submit("/mcp add --name notion-mcp --url https://a/sse")
    assert "already exists" in _errors(c)
    assert c.unapproved == set()
    assert "notion-mcp" in _registry_names(c.tools)


async def test_a_server_removed_in_the_session_leaves_its_tools_at_once(home, monkeypatch):
    calls = patch_build(monkeypatch, mock_runtime())
    seed_server(home)
    c = _controller(home)
    assert "notion-mcp" in _registry_names(c.tools)
    await c.submit("/mcp remove notion-mcp --yes")
    assert "notion-mcp" not in _registry_names(c.tools)
    assert "notion-mcp" not in _registry_names(calls[-1]["tool_registry"])


async def test_a_model_reply_asking_to_approve_a_server_does_nothing(home, monkeypatch):
    """A slash command only ever comes from the input box: text in a reply is just text."""
    patch_build(monkeypatch, mock_runtime())
    record = create_agent(home.root)
    c = make_controller(home.root, record, mock_runtime(chunks=["/mcp approve notion-mcp --all"]))
    await c.submit("/mcp add --name notion-mcp --url https://a/sse")
    await c.submit("please connect my notes")
    assert c.unapproved == {"notion-mcp"}
    assert "notion-mcp" not in _registry_names(c.tools)


# ── parity: a slash command and its one-shot command persist the same ─────


def _normalised(path: Path) -> Any:
    """A JSON file with the per-run fields (ids, timestamps) blanked out."""
    volatile = {"id", "created_at", "updated_at", "last_active"}

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: "*" if k in volatile else scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return scrub(json.loads(path.read_text()))


def _agent_files(w: World) -> list[Any]:
    return sorted((_normalised(p) for p in w.agents.glob("*/agent.json")), key=lambda a: a["name"])


PARITY = [
    (
        ["providers", "add", "--provider", "anthropic", "--model-id", "claude-x", "--name", "work"],
        f"{API_KEY}\n",
        [API_KEY],
    ),
    (
        [
            "providers",
            "add",
            "--provider",
            "ollama",
            "--model-id",
            "llama3",
            "--name",
            "local",
            "--endpoint",
            "http://h",
        ],
        "",
        [],
    ),
    (["agent", "create", "--name", "oracle", "--card", "priestess", "--model", "ollama/hermes-3"], "", []),
    (
        ["mcp", "add", "--name", "notion-mcp", "--url", "https://mcp.notion.com/sse", "--header", "Authorization="],
        f"{BEARER}\n",
        [BEARER],
    ),
    (["mcp", "remove", "notion-mcp", "--yes"], "", []),
]


def _prepare(root: Path, monkeypatch: pytest.MonkeyPatch, *, with_server: bool) -> World:
    use_arcana_home(root, monkeypatch)
    w = install_world(root, monkeypatch)
    create_agent(w.root)
    if with_server:
        seed_server(w)
    return w


def _state(w: World) -> Any:
    files = [w.root / "connections" / name for name in ("models.json", "mcps.json")]
    return [_normalised(f) if f.exists() else None for f in files], _agent_files(w), dict(w.keyring)


def _as_slash(argv: list[str]) -> str:
    return "/" + " ".join(shlex.quote(a) for a in argv)


@pytest.mark.parametrize(("argv", "typed", "answers"), PARITY, ids=[" ".join(p[0][:2]) for p in PARITY])
def test_a_slash_command_persists_what_its_one_shot_command_does(tmp_path, monkeypatch, argv, typed, answers):
    with_server = argv[:2] == ["mcp", "remove"]
    one_shot = _prepare(tmp_path / "one-shot", monkeypatch, with_server=with_server)
    result = runner.invoke(app, argv, input=typed)
    assert result.exit_code == 0, result.output

    session = _prepare(tmp_path / "session", monkeypatch, with_server=with_server)
    before = _state(session)
    record = AgentRegistry(session.agents).list()[0]
    c = make_controller(session.root, record, mock_runtime(), renderer=RecordingRenderer(answers=answers))
    asyncio.run(c.submit(_as_slash(argv)))
    assert _state(session) != before  # the slash command did something

    for name in ("models.json", "mcps.json"):
        a, b = one_shot.root / "connections" / name, session.root / "connections" / name
        assert a.exists() == b.exists()
        if a.exists():
            assert _normalised(a) == _normalised(b)
    assert _agent_files(session) == _agent_files(one_shot)
    assert sorted(session.keyring.values()) == sorted(one_shot.keyring.values())


#: Read-only commands whose session output must be the one-shot command's, document for document.
OUTPUT_PARITY = [
    ["agent", "list"],
    ["agent", "show", "scout"],
    ["providers", "list"],
    ["mcp", "list"],
    ["mcp", "show", "notion-mcp"],
    ["tools", "list", "--agent", "scout"],
    ["cards", "show", "hermit"],
    ["soul", "show"],
    ["memory", "adapters"],
    ["status"],
]


@pytest.mark.parametrize("argv", OUTPUT_PARITY, ids=[" ".join(a) for a in OUTPUT_PARITY])
def test_a_slash_command_shows_what_its_one_shot_command_does(tmp_path, monkeypatch, argv):
    """The session gets the same result the one-shot command's ``--json`` prints, from the same state."""
    w = _prepare(tmp_path, monkeypatch, with_server=True)
    result = runner.invoke(app, [*argv, "--json"])
    assert result.exit_code == 0, result.output
    one_shot = json.loads(result.stdout)

    record = AgentRegistry(w.agents).list()[0]
    c = make_controller(w.root, record, mock_runtime())
    asyncio.run(c.submit(_as_slash(argv)))
    assert isinstance(c.renderer, RecordingRenderer)
    assert c.renderer.errors == []
    assert c.renderer.documents() == [one_shot]
