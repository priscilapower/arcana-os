"""The prompt sites as renderer-agnostic coroutines, driven by a scripted renderer.

Every question a command asks goes through the renderer it is handed and names
the option that answers it without a prompt; a secret answer reaches the
keyring and nothing else, on every adapter.
"""

import logging

import pytest
import typer
from textual.widgets import Input

from arcana.agents.registry import AgentRegistry
from arcana.models import ConnectionStore
from arcana.types.card import Card
from arcana.types.model import ModelProvider
from arcana_cli.commands.agent import TYPE_A_REFERENCE, create_agent, delete_agent, edit_agent
from arcana_cli.commands.mcp import add_server, remove_server
from arcana_cli.commands.providers import add_provider, edit_provider, remove_provider
from arcana_cli.commands.tools import unsubscribe
from arcana_cli.tui.screens import PromptScreen
from arcana_cli.ui.renderer import CANCELLED, JsonRenderer, NonInteractiveError
from tests.support.renderer import RecordingRenderer
from tests.support.tui import arcana_pilot
from tests.support.world import World, seed_agent, seed_connection, seed_ollama, seed_server

SECRET = "sk-live-TOPSECRET-42"


def _flags(r: RecordingRenderer) -> list[str | None]:
    return [q.flag for q in r.questions]


def _add_provider_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "provider": None,
        "model_id": None,
        "name": None,
        "endpoint": None,
        "api_key": None,
        "api_key_env": None,
        "oauth": False,
        "issuer": None,
        "scope": [],
        "device": False,
        "yes": False,
    }
    return args | overrides


def _add_server_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "name": "notion-mcp",
        "url": "https://mcp.notion.com/sse",
        "command": None,
        "args": [],
        "transport": None,
        "header": ["Authorization="],
        "auth_key": None,
        "oauth": False,
        "issuer": None,
        "scope": [],
        "device": False,
        "description": "",
    }
    return args | overrides


# ── agent create: the wizard and the model picker ─────────────────────────


async def test_create_agent_names_the_flag_for_every_question(world: World):
    seed_ollama(world)
    conn = ConnectionStore(world.models).all()[0]
    r = RecordingRenderer(answers=["scout", ""], confirms=[False], selections=[Card.HERMIT, conn])

    await create_agent(r, name=None, card=None, model=None)

    assert _flags(r) == ["--name", "--model"]
    assert [o["flag"] for o in r.select_options] == ["--card", "--model"]
    assert AgentRegistry(world.agents).list()[0].model == "ollama:ollama/hermes-3/hermes-3"  # blank kept the default


async def test_the_model_picker_offers_each_connection_then_a_typed_reference(world: World):
    seed_ollama(world)
    seed_connection(world, "work", ModelProvider.ANTHROPIC, "claude-sonnet-4-6")
    conns = {c.name: c for c in ConnectionStore(world.models).all()}
    r = RecordingRenderer(answers=["claude-opus-4-8"], selections=[conns["work"]])

    await create_agent(r, name="scout", card="hermit", model=None)

    (offered,) = r.offered
    assert [c.value for c in offered] == [conns["ollama/hermes-3"], conns["work"], TYPE_A_REFERENCE]
    assert r.select_options[0]["initial"] == [conns["ollama/hermes-3"]]  # Enter keeps the first, as before
    assert AgentRegistry(world.agents).list()[0].model == "anthropic:work/claude-opus-4-8"


async def test_a_typed_model_reference_is_validated_and_stored_verbatim(world: World):
    seed_ollama(world)
    r = RecordingRenderer(answers=["", "open ai/gpt", "openai/gpt-5"], selections=[TYPE_A_REFERENCE])

    await create_agent(r, name="scout", card="hermit", model=None)

    assert r.rejections == ["An answer is required.", "A model reference has no spaces."]
    assert AgentRegistry(world.agents).list()[0].model == "openai/gpt-5"


async def test_cancelling_the_model_picker_creates_nothing(world: World):
    seed_ollama(world)
    r = RecordingRenderer(selections=[None])
    with pytest.raises(typer.Exit) as exited:
        await create_agent(r, name="scout", card="hermit", model=None)
    assert exited.value.exit_code == 0
    assert AgentRegistry(world.agents).list() == []


async def test_create_agent_under_json_refuses_naming_the_model_flag(world: World):
    seed_ollama(world)
    with pytest.raises(NonInteractiveError) as refused:
        await create_agent(JsonRenderer(), name="scout", card="hermit", model=None)
    assert refused.value.flag == "--model"


# ── agent edit / delete ────────────────────────────────────────────────────


async def test_edit_agent_names_the_flag_for_every_question(world: World):
    seed_agent(world)
    r = RecordingRenderer(
        answers=["ranger", "A quiet scout", "bad model", "", "field, research"],
        confirms=[False],
        selections=[Card.HERMIT],
    )

    await edit_agent(r, "scout", new_name=None, description=None, card=None, model=None, tags=None)

    assert _flags(r) == ["--name", "--description", "--model", "--tags"]
    assert r.rejections == ["A model reference has no spaces."]
    record = AgentRegistry(world.agents).list()[0]
    assert (record.name, record.model, record.tags) == (
        "ranger",
        "ollama:ollama/hermes-3/hermes-3",
        ["field", "research"],
    )


async def test_delete_agent_declined_is_cancelled_with_exit_1(world: World):
    seed_agent(world)
    r = RecordingRenderer(confirms=[False])
    with pytest.raises(typer.Exit) as exited:
        await delete_agent(r, "scout", yes=False)
    assert exited.value.exit_code == 1
    assert CANCELLED in r.notes_text()
    assert [a.name for a in AgentRegistry(world.agents).list()] == ["scout"]


async def test_delete_agent_with_yes_never_asks(world: World):
    seed_agent(world)
    r = RecordingRenderer()
    await delete_agent(r, "scout", yes=True)
    assert r.confirmations == []
    assert AgentRegistry(world.agents).list() == []


# ── providers ──────────────────────────────────────────────────────────────


async def test_add_provider_names_the_flag_for_every_question(world: World):
    r = RecordingRenderer(answers=["OpenAI-Compat", "hermes-3", "local", "http://gpu:8000", ""])
    await add_provider(r, **_add_provider_args())
    assert _flags(r) == ["--provider", "--model-id", "--name", "--endpoint", "--api-key-env"]
    assert [q.secret for q in r.questions] == [False, False, False, False, True]
    assert ConnectionStore(world.models).get_by_name("local") is not None


async def test_add_provider_re_asks_an_unknown_provider(world: World):
    r = RecordingRenderer(answers=["claude", "ollama", "hermes-3", "", ""])
    await add_provider(r, **_add_provider_args())
    assert r.rejections == ["Unknown provider. Choose from: ollama, anthropic, openai, openai_compat, custom"]


async def test_add_provider_overwrite_asks_unless_yes(world: World):
    seed_ollama(world)
    args = _add_provider_args(provider="ollama", model_id="hermes-3", name="ollama/hermes-3", endpoint="http://gpu")

    declined = RecordingRenderer(confirms=[False])
    with pytest.raises(typer.Exit) as exited:
        await add_provider(declined, **args)
    assert exited.value.exit_code == 0
    assert ConnectionStore(world.models).all()[0].endpoint == "http://localhost:11434"

    forced = RecordingRenderer()
    await add_provider(forced, **(args | {"yes": True}))
    assert forced.confirmations == []
    assert ConnectionStore(world.models).all()[0].endpoint == "http://gpu"


async def test_edit_provider_interactive_names_its_flags(world: World):
    seed_connection(world, "work", ModelProvider.OPENAI, "gpt-4")
    r = RecordingRenderer(answers=["", SECRET], confirms=[True])
    await edit_provider(r, "work", base_url=None, rotate_key=False, api_key_env=None, header=None, no_verify=True)
    assert _flags(r) == ["--base-url", "--api-key-env"]
    assert r.questions[1].secret
    assert SECRET in world.keyring.values()


async def test_remove_provider_declined_is_cancelled_with_exit_1(world: World):
    seed_ollama(world)
    r = RecordingRenderer(confirms=[False])
    with pytest.raises(typer.Exit) as exited:
        await remove_provider(r, "ollama/hermes-3", yes=False, force=False)
    assert exited.value.exit_code == 1
    assert ConnectionStore(world.models).all() != []


# ── mcp / tools ────────────────────────────────────────────────────────────


async def test_the_bearer_question_is_secret_names_auth_key_and_rejects_blank(world: World):
    r = RecordingRenderer(answers=["   ", SECRET])
    await add_server(r, **_add_server_args())
    (q,) = r.questions
    assert (q.secret, q.flag) == (True, "--auth-key")
    assert r.rejections == ["No bearer token provided."]  # the rejection never quotes the answer
    assert world.keyring == {"mcp_notion-mcp_auth": SECRET}


async def test_remove_server_and_unsubscribe_confirm_through_the_renderer(world: World):
    seed_server(world)
    seed_agent(world, subs=["builtin/web_search"])
    r = RecordingRenderer(confirms=[True, True])
    await remove_server(r, "notion-mcp", yes=False, force=False)
    await unsubscribe(r, "scout", "builtin/web_search", yes=False)
    assert r.confirmations == ["Remove MCP server 'notion-mcp'?", "Unsubscribe 'scout' from 'builtin/web_search'?"]


# ── secrets, per adapter ───────────────────────────────────────────────────


async def test_a_secret_answer_never_reaches_output_notes_or_the_log(world: World, caplog: pytest.LogCaptureFixture):
    """Recording adapter: the key reaches the keyring and no other surface."""
    caplog.set_level(logging.DEBUG)
    r = RecordingRenderer(answers=[SECRET])
    await add_provider(r, **_add_provider_args(provider="anthropic", model_id="claude", name="work"))
    assert world.keyring  # stored …
    assert SECRET in world.keyring.values()
    assert SECRET not in r.text() and SECRET not in r.notes_text()  # … and nowhere else
    assert SECRET not in str(r.documents())
    assert all(SECRET not in repr(q) for q in r.questions)
    assert SECRET not in caplog.text


async def test_json_never_asks_for_a_secret(world: World):
    """Json adapter: the question fails closed, so there is no answer to leak."""
    with pytest.raises(NonInteractiveError) as refused:
        await add_server(JsonRenderer(), **_add_server_args())
    assert refused.value.flag == "--auth-key"
    assert world.keyring == {}


async def test_textual_masks_the_api_key_and_keeps_it_out_of_the_transcript(
    world: World, caplog: pytest.LogCaptureFixture
):
    """Textual adapter: the same command body in the session; the key is typed into a password field."""
    caplog.set_level(logging.DEBUG)
    async with arcana_pilot() as h:
        worker = h.start(
            add_provider(h.renderer, **_add_provider_args(provider="anthropic", model_id="claude", name="work"))
        )
        screen = await h.wait_for_screen(PromptScreen)
        assert screen.query_one(Input).password is True
        await h.pilot.press(*SECRET, "enter")
        await worker.wait()
        await h.pilot.pause()
        assert "Added connection 'work'" in h.retained_text()
        assert SECRET not in h.retained_text()
        assert SECRET not in h.visible_text()
    assert SECRET in world.keyring.values()
    assert SECRET not in caplog.text
