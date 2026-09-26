"""The CLI half of OAuth sign-in: what the user is shown, through the renderer, and nothing else.

The flows run against a fake authorization server; the loopback flow keeps its
real ``127.0.0.1`` listener, driven by a stand-in browser.
"""

import asyncio
from typing import Any

import pytest
import typer

from arcana.types.auth import OAuthConfig
from arcana_cli._oauth import SIGN_IN_TITLE, WAITING_FOR_AUTHORIZATION, sign_in, sign_in_or_exit
from tests.support.oauth import ACCESS_TOKEN, ISSUER, USER_CODE, install_fake_as, listener_is_closed
from tests.support.renderer import RecordingRenderer

CONFIG = OAuthConfig(issuer=ISSUER)


async def test_device_sign_in_shows_the_code_while_it_waits(monkeypatch):
    server, _ = install_fake_as(monkeypatch)
    server.approved = True
    r = RecordingRenderer()
    token, resolved = await sign_in(r, CONFIG, device=True)
    assert token.access_token == ACCESS_TOKEN
    assert resolved.client_id == "dyn-client"
    assert r.waits == [WAITING_FOR_AUTHORIZATION]
    assert USER_CODE in r.shown_text()
    assert "Signed in" in r.notes_text()
    assert ACCESS_TOKEN not in r.text() + r.notes_text() + r.shown_text()


async def test_browser_sign_in_shows_the_url_and_closes_its_loopback_listener(monkeypatch):
    _, browser = install_fake_as(monkeypatch, complete_browser=True)
    r = RecordingRenderer()
    token, _ = await sign_in(r, CONFIG, device=False)
    assert token.access_token == ACCESS_TOKEN
    assert browser.redirect_uri.startswith("http://127.0.0.1:")
    assert browser.opened[-1] in r.shown_text(width=1000)  # the URL, to open by hand
    assert await listener_is_closed(browser.redirect_uri)


async def test_a_wait_called_off_aborts_and_closes_the_listener(monkeypatch):
    install_fake_as(monkeypatch, complete_browser=False)
    servers: list[asyncio.AbstractServer] = []
    start_server = asyncio.start_server

    async def recording_start_server(*args: Any, **kwargs: Any) -> asyncio.AbstractServer:
        server = await start_server(*args, **kwargs)
        assert [sock.getsockname()[0] for sock in server.sockets] == ["127.0.0.1"]  # loopback only
        servers.append(server)
        return server

    monkeypatch.setattr(asyncio, "start_server", recording_start_server)
    r = RecordingRenderer(call_off_waits=True)
    with pytest.raises(typer.Abort):
        await sign_in(r, CONFIG, device=False)
    [server] = servers
    assert not server.is_serving()
    assert "Signed in" not in r.notes_text()


async def test_a_failed_sign_in_is_an_error_and_an_exit_with_the_given_code(monkeypatch):
    install_fake_as(monkeypatch)
    r = RecordingRenderer()
    with pytest.raises(typer.Exit) as exc:
        await sign_in_or_exit(r, OAuthConfig(issuer=ISSUER, metadata_url=f"{ISSUER}/nowhere"), device=True, code=7)
    assert exc.value.exit_code == 7
    [failure] = r.errors
    assert failure.code == 7
    assert "OAuth sign-in failed" in r.errors_text()


async def test_a_called_off_sign_in_is_not_reported_as_a_failure(monkeypatch):
    install_fake_as(monkeypatch)
    r = RecordingRenderer(call_off_waits=True)
    with pytest.raises(typer.Abort):
        await sign_in_or_exit(r, CONFIG, device=True, code=1)
    assert "failed" not in r.notes_text()
    assert r.errors == []


def test_the_wait_is_titled_for_sign_in():
    assert SIGN_IN_TITLE == "Sign in"
