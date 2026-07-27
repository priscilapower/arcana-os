"""Tests for `arcana providers login` — re-auth of an existing OAuth connection.

The interactive OAuth flow is stubbed (no browser / network); the test asserts
the command reuses the stored config, refreshes the keyring token in place, and
never asks the user to re-`add`.
"""

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

import arcana_cli.commands.providers as providers_mod
from arcana.models import ConnectionStore
from arcana.types.auth import AuthType, OAuthConfig, OAuthToken
from arcana.types.model import ModelConnection, ModelProvider
from arcana_cli.main import app

runner = CliRunner()


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    monkeypatch.setattr(providers_mod, "CONNECTIONS_PATH", path)
    return path


def _seed(path, **kw) -> ModelConnection:
    store = ConnectionStore(path)
    conn = ModelConnection(name="claude", provider=ModelProvider.ANTHROPIC, **kw)
    store.upsert(conn)
    return store.get_by_name("claude")


def _stub_sign_in(monkeypatch, token: OAuthToken, config: OAuthConfig):
    async def _fake(cfg, *, device, console, client_factory=None):
        return token, config

    monkeypatch.setattr(providers_mod, "sign_in", _fake)


def test_login_refreshes_token_for_existing_oauth_connection(store_path, monkeypatch):
    cfg = OAuthConfig(issuer="https://auth.example.com", client_id="c1")
    _seed(store_path, auth_type=AuthType.OAUTH, oauth_config=cfg, credential_ref="claude_oauth_token")

    saved: dict[str, str] = {}
    monkeypatch.setattr("keyring.set_password", lambda s, r, v: saved.__setitem__(r, v))
    new_token = OAuthToken(access_token="fresh", refresh_token="r2", expires_at=datetime(2999, 1, 1, tzinfo=UTC))
    _stub_sign_in(monkeypatch, new_token, cfg)

    result = runner.invoke(app, ["providers", "login", "claude"])
    assert result.exit_code == 0, result.output
    assert "Signed in" in result.output
    # The refreshed token landed in the keyring under the connection's ref.
    assert "fresh" in saved["claude_oauth_token"]


def test_login_rejects_api_key_connection(store_path, monkeypatch):
    _seed(store_path)  # default auth_type=api_key
    result = runner.invoke(app, ["providers", "login", "claude"])
    assert result.exit_code == 1
    assert "API key" in result.output
    assert "--rotate-key" in result.output


def test_login_unknown_connection_exits_nonzero(store_path):
    result = runner.invoke(app, ["providers", "login", "nope"])
    assert result.exit_code == 1
