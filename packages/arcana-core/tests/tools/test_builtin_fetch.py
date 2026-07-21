"""Tests for the fetch_url handler — content handling, caps, and failure mapping.

All network is mocked with ``httpx.MockTransport``; a public literal host keeps
DNS out of the picture so the SSRF guard is exercised without resolving names.
"""

from typing import Any

import httpx

from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.web.config import WebToolsConfig
from tests.support.tools import PUBLIC_IP, make_mock_client


def _adapter(client: httpx.AsyncClient, **cfg: Any) -> BuiltinToolAdapter:
    return BuiltinToolAdapter(WebToolsConfig(**cfg), http=client)


async def test_html_reduced_to_main_text():
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "<html><head><title>t</title><style>.x{}</style></head>"
            "<body><script>ignore()</script><p>Hello world</p></body></html>"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/page"})

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["status_code"] == 200
    assert result.output["truncated"] is False
    assert result.output["text"] == "Hello world"  # script/style/head dropped


async def test_json_passed_through_decoded():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='{"a": 1}', headers={"content-type": "application/json"})

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/data"})

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["text"] == '{"a": 1}'


async def test_unsupported_binary_type_returns_placeholder():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG\r\n", headers={"content-type": "image/png"})

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/img"})

    assert result.success is True
    assert isinstance(result.output, dict)
    text = result.output["text"]
    assert isinstance(text, str) and "unsupported content type" in text


async def test_oversize_body_marked_truncated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/plain"})

    async with make_mock_client(handler) as client:
        result = await _adapter(client, fetch_max_bytes=100).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/big"})

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["truncated"] is True


async def test_http_404_is_a_successful_tool_result():
    # An HTTP error is not a tool error — the model gets the status and body.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found", headers={"content-type": "text/plain"})

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/missing"})

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["status_code"] == 404
    assert result.output["text"] == "Not Found"


async def test_terminal_3xx_without_location_is_returned_not_blocked():
    # A 304 (or 300/305) carries no Location — it is a real response the model
    # should see, not a redirect to follow and not an SSRF block.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304, headers={"content-type": "text/plain"})

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/cached"})

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["status_code"] == 304


async def test_timeout_maps_to_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/slow"})

    assert result.success is False
    assert result.error == "timeout"


async def test_transport_error_maps_to_fetch_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": f"http://{PUBLIC_IP}/x"})

    assert result.success is False
    assert result.error is not None and result.error.startswith("fetch failed")


async def test_blocked_private_url_never_requests():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="secret")

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {"url": "http://169.254.169.254/latest"})

    assert result.success is False
    assert result.error is not None and result.error.startswith("blocked:")
    assert calls == []  # no request was ever issued


async def test_missing_url_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with make_mock_client(handler) as client:
        result = await _adapter(client).execute("fetch_url", {})
    assert result.success is False
    assert result.error is not None and "url" in result.error
