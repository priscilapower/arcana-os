"""Tests for the network-egress safety envelope — scheme + SSRF + caps."""

import ipaddress
from typing import Any

import httpx
import pytest

from arcana.tools.builtins.web import egress
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.builtins.web.egress import EgressBlocked, check_url, guarded_get
from tests.support.tools import PUBLIC_IP, make_mock_client

_IP = ipaddress.IPv4Address | ipaddress.IPv6Address


def _cfg(**kw: Any) -> WebToolsConfig:
    return WebToolsConfig(**kw)


# ---------------------------------------------------------------------------
# Scheme allow-list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "data:text/plain,hi"],
)
async def test_non_http_schemes_blocked(url: str):
    with pytest.raises(EgressBlocked):
        await check_url(url, _cfg())


async def test_missing_host_blocked():
    with pytest.raises(EgressBlocked):
        await check_url("http:///no-host", _cfg())


# ---------------------------------------------------------------------------
# SSRF — literal addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
async def test_private_and_metadata_literals_blocked(url: str):
    with pytest.raises(EgressBlocked):
        await check_url(url, _cfg())


async def test_localhost_name_blocked():
    with pytest.raises(EgressBlocked):
        await check_url("http://localhost/", _cfg())


async def test_public_literal_allowed():
    # Must not raise.
    await check_url(f"http://{PUBLIC_IP}/", _cfg())


async def test_hostname_resolving_to_private_ip_blocked(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve(host: str) -> list[_IP]:
        return [ipaddress.ip_address("10.1.2.3")]

    monkeypatch.setattr(egress, "_resolve_host", fake_resolve)
    with pytest.raises(EgressBlocked):
        await check_url("http://sneaky.example.com/", _cfg())


async def test_allow_private_hosts_bypasses_ssrf_but_not_scheme():
    cfg = _cfg(allow_private_hosts=True)
    await check_url("http://127.0.0.1/", cfg)  # allowed with the escape hatch
    with pytest.raises(EgressBlocked):
        await check_url("file:///etc/passwd", cfg)  # scheme still enforced


# ---------------------------------------------------------------------------
# guarded_get — redirects, caps
# ---------------------------------------------------------------------------


async def test_redirect_to_private_ip_blocked_on_second_hop():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        return httpx.Response(200, text="should never reach here")

    async with make_mock_client(handler) as client:
        with pytest.raises(EgressBlocked):
            await guarded_get(client, f"http://{PUBLIC_IP}/redirect", _cfg())


async def test_redirect_cap_exceeded_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"http://{PUBLIC_IP}/loop"})

    async with make_mock_client(handler) as client:
        with pytest.raises(EgressBlocked, match="too many redirects"):
            await guarded_get(client, f"http://{PUBLIC_IP}/loop", _cfg(max_redirects=1))


async def test_streamed_body_capped_and_flagged_truncated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 500, headers={"content-type": "text/plain"})

    async with make_mock_client(handler) as client:
        resp = await guarded_get(client, f"http://{PUBLIC_IP}/big", _cfg(fetch_max_bytes=100))
    assert resp.truncated is True
    assert len(resp.content) == 100


async def test_successful_fetch_returns_body_untruncated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})

    async with make_mock_client(handler) as client:
        resp = await guarded_get(client, f"http://{PUBLIC_IP}/ok", _cfg())
    assert resp.status_code == 200
    assert resp.truncated is False
    assert resp.content == b"hello"
