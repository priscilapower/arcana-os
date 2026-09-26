"""The setup wizards inside the session: ``/agent``, ``/providers`` and ``/mcp``.

Each slash command runs the one-shot command's own coroutine, so these tests
check the session's side of it: arguments parse with the one-shot options, a
wizard's failure is a note (never the end of the session), a secret can't ride
on the command line, the session picks up what a wizard changed, and an MCP
server added in the session stays out of the running agent's tools until it is
approved. The controller runs over a :class:`RecordingRenderer`; the dialogs
themselves are covered by ``test_chat_wizards_app.py``.
"""

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.agent as agent_mod
import arcana_cli.commands.mcp as mcp_mod
from arcana.agents.registry import AgentRegistry
from arcana.models import ConnectionStore
from arcana.tools.registry import MCPRegistry
from arcana.types.card import Card
from arcana_cli.commands.chat.controller import SECRET_ON_THE_LINE, _ChatController, _keeps_in_history
from arcana_cli.commands.chat.wizards import HIDDEN, WIZARDS, WizardUsageError, find_wizard, redacted
from arcana_cli.main import app
from arcana_cli.ui.input_model import _SLASH_SUBCOMMANDS
from tests.support.chat import FakeGateway, create_agent, make_controller, mock_runtime, patch_build, use_arcana_home
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


def _registry_names(registry: MCPRegistry) -> set[str]:
    return {server.name for server in registry.list_servers()}


# ── the registry ──────────────────────────────────────────────────────────


def test_every_sub_action_has_a_wizard_and_every_wizard_a_sub_action():
    assert set(WIZARDS) == {(command, action) for command, actions in _SLASH_SUBCOMMANDS.items() for action in actions}


def test_arguments_parse_with_the_one_shot_commands_options():
    wizard, args = find_wizard("/providers add --provider ollama --scope a --scope b -y")
    assert wizard is WIZARDS[("/providers", "add")]
    params = wizard.parse(args)
    assert (params["provider"], tuple(params["scope"]), params["yes"]) == ("ollama", ("a", "b"), True)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("/mcp add", "Missing option '--name'"),
        ("/agent delete", "Missing argument"),
        ("/providers add --bogus", "No such option: --bogus"),
        ("/providers add --help", "No such option: --help"),
        ("/agent create --name 'unbalanced", "No closing quotation"),
        ("/mcp remove notion-mcp --json", "--json isn't available inside the session"),
    ],
)
def test_arguments_that_dont_parse_say_why_and_print_nothing(line, message, capsys):
    wizard, args = find_wizard(line)
    assert wizard is not None
    with pytest.raises(WizardUsageError, match=message):
        wizard.parse(args)
    assert capsys.readouterr() == ("", "")


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
    ],
)
def test_a_secret_on_the_line_is_redacted(line, shown):
    assert redacted(line) == shown
    assert not _keeps_in_history(line)


@pytest.mark.parametrize(
    "line",
    [
        "/providers add --api-key-env MY_KEY",
        "/mcp add --name n --url https://a/sse --header Authorization=",
        "/mcp add --name n --url https://a/sse --header 'Authorization=Bearer '",
        "/providers edit anthropic --rotate-key",
        f"/switch {API_KEY}",
        f"tell me about --api-key {API_KEY}",
    ],
)
def test_a_line_without_a_secret_option_is_left_alone(line):
    assert redacted(line) is None
    assert _keeps_in_history(line)


# ── dispatch and failures ─────────────────────────────────────────────────


@pytest.mark.parametrize("line", ["/agent", "/providers frobnicate", "/mcp approve-all"])
async def test_a_missing_or_unknown_sub_action_shows_the_usage(home, line):
    c = _controller(home)
    await c.submit(line)
    command = line.split(" ")[0]
    assert f"Usage: {command} {'|'.join(_SLASH_SUBCOMMANDS[command])}" in _out(c)
    assert not c.exited


async def test_a_usage_error_names_the_one_shot_command_to_ask_about(home):
    c = _controller(home)
    await c.submit("/mcp add --url https://a/sse")
    out = _out(c)
    assert "/mcp add: Missing option '--name'" in out
    assert "arcana mcp add --help" in out


async def test_a_wizard_that_exits_with_an_error_leaves_the_session_running(home):
    c = _controller(home)
    await c.submit("/agent delete ghost")
    assert "No agent 'ghost'." in _out(c)
    assert not c.exited
    await c.submit("/help")  # still taking commands
    assert "In-session commands" in _out(c)


async def test_a_wizard_that_raises_becomes_an_error_note(home, monkeypatch):
    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(agent_mod, "delete_agent", boom)
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
            json_=False,
        )
    assert home.keyring == {}
    assert not home.mcps.exists() or "notion-mcp" not in home.mcps.read_text()


# ── the session picks up what a wizard changed ───────────────────────────


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


async def test_a_providers_wizard_refreshes_the_sessions_connections(home):
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
    assert "already exists" in _notes(c)
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
