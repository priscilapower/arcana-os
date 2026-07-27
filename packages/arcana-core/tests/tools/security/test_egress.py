"""Finding ``ssrf`` — network egress is refused before any request leaves.

``fetch_url`` takes a URL the model chose, so a hallucinated or injected argument
can aim at cloud metadata (``169.254.169.254``), loopback, or a private service.
Table-driven: hostile URLs, each expected to raise :class:`EgressBlocked` from the
real guard *before* a request is built — proven by a transport handler that
records every request it sees and asserting it saw none. The redirect case shows
the guard re-runs on each hop: a public URL that 302s to metadata is caught on the
second hop, after the transport has been used, so the *metadata* host is the one
never contacted.

Covers the SSRF / egress guard shared by the network builtins.
"""

import ipaddress
from typing import Any

import httpx
import pytest

from arcana.tools.builtins.web import egress as egress_module
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.builtins.web.egress import EgressBlocked, check_url, guarded_get
from tests.support.tools import PUBLIC_IP, make_mock_client

pytestmark = pytest.mark.security

#: Guards this module discharges — see ``security/catalog.py``.
COVERS = frozenset({"builtin:egress"})

#: URLs a fetch must never reach — the address ranges plus the disallowed schemes.
BLOCKED_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP link-local metadata
    "http://127.0.0.1/",  # loopback
    "http://10.0.0.5/",  # private range
    "http://192.168.1.1/",  # private range
    "http://[::1]/",  # IPv6 loopback
    "file:///etc/passwd",  # non-http scheme
    "gopher://169.254.169.254/",  # non-http scheme
]


def _cfg(**overrides: Any) -> WebToolsConfig:
    return WebToolsConfig(**overrides)


@pytest.mark.parametrize("url", BLOCKED_URLS)
async def test_check_url_blocks_before_any_request(url: str):
    with pytest.raises(EgressBlocked):
        await check_url(url, _cfg())


async def test_hostname_resolving_to_metadata_is_blocked(monkeypatch: pytest.MonkeyPatch):
    """A name that resolves to a private IP is refused — DNS rebinding's front door."""

    async def _fake_resolve(host: str):
        return [ipaddress.ip_address("169.254.169.254")]

    monkeypatch.setattr(egress_module, "_resolve_host", _fake_resolve)
    with pytest.raises(EgressBlocked, match="blocked address"):
        await check_url("http://innocent.example.com/", _cfg())


async def test_no_request_is_ever_issued_for_a_blocked_url():
    """The guard refuses without touching the transport at all."""
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, text="should never be reached")

    client = make_mock_client(handler)
    async with client:
        with pytest.raises(EgressBlocked):
            await guarded_get(client, "http://169.254.169.254/latest/meta-data/", _cfg())
    assert seen == []  # the metadata host was never contacted


async def test_redirect_to_metadata_is_blocked_on_the_second_hop():
    """A public URL that 302s to metadata is caught before the metadata GET."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="metadata")  # reached only if the guard fails

    client = make_mock_client(handler)
    async with client:
        with pytest.raises(EgressBlocked, match="blocked address"):
            await guarded_get(client, f"http://{PUBLIC_IP}/redirect", _cfg())
    # The first hop (the public host) was contacted; the metadata host never was.
    assert "169.254.169.254" not in seen
