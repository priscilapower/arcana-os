"""The network-egress safety envelope shared by the network builtins.

``fetch_url`` takes a URL chosen by the model, so a hallucinated or injected
argument can point at ``http://169.254.169.254/…`` (cloud metadata) or
``http://localhost:6379`` (a local service). This module is the always-on floor
that makes egress safe to *run*:

* scheme allow-list — only ``http`` / ``https``;
* SSRF guard — the resolved host must not map to a private, loopback,
  link-local, reserved, multicast, or unspecified address, re-checked on **every
  redirect hop** (redirects are followed manually for exactly this reason);
* streamed byte cap — the body is read in chunks and aborted past the limit,
  flagged ``truncated``, so a huge page can't exhaust memory.

Everything fails *closed* by raising :class:`EgressBlocked`, which the caller
turns into a ``ToolResult(success=False, …)`` — no request leaves the process
until the checks pass.
"""

import asyncio
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from arcana.tools.builtins.web.config import WebToolsConfig

# Only these schemes ever reach the network; file:, ftp:, gopher:, data: … are out.
SCHEME_ALLOWLIST: frozenset[str] = frozenset({"http", "https"})

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class EgressBlocked(Exception):
    """A URL was refused before any request was issued. ``reason`` is model-safe."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class GuardedResponse:
    """The bytes and metadata of a fetch that cleared the guard."""

    final_url: str
    status_code: int
    headers: httpx.Headers
    content: bytes
    truncated: bool
    encoding: str | None


def _ip_is_blocked(ip: _IPAddress) -> bool:
    """True for any address a fetch must never reach.

    ``is_private`` already subsumes loopback and link-local on CPython, but the
    checks are spelled out so the intent survives a stdlib change. IPv4-mapped
    IPv6 (``::ffff:127.0.0.1``) is unwrapped and re-checked so it can't smuggle a
    private v4 address through.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None and _ip_is_blocked(ip.ipv4_mapped):
        return True
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


async def _resolve_host(host: str) -> list[_IPAddress]:
    """Resolve ``host`` to every IP it maps to.

    A literal IP (in any notation ``ipaddress`` accepts) is returned directly;
    otherwise DNS runs via the event loop's resolver. Encoded-integer hosts (e.g.
    ``http://2130706433/``) are not literals to ``ipaddress`` but resolve through
    ``getaddrinfo`` to their real address, which is then range-checked. Patched in
    tests to simulate a name resolving to a private IP.
    """
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    # sockaddr[0] is the numeric address string for both AF_INET and AF_INET6.
    return [ipaddress.ip_address(info[4][0]) for info in infos]


async def check_url(url: str, cfg: WebToolsConfig) -> None:
    """Raise :class:`EgressBlocked` unless ``url`` is safe to request.

    Enforces the scheme allow-list always; the SSRF address check is skipped only
    when ``cfg.allow_private_hosts`` is on (the local-dev escape hatch).
    """
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in SCHEME_ALLOWLIST:
        raise EgressBlocked(f"scheme not allowed: {scheme or '(none)'}")

    host = parsed.hostname
    if not host:
        raise EgressBlocked("missing host")

    if cfg.allow_private_hosts:
        return

    try:
        ips = await _resolve_host(host)
    except (OSError, ValueError) as exc:
        raise EgressBlocked(f"could not resolve host: {host}") from exc

    if not ips:
        raise EgressBlocked(f"could not resolve host: {host}")

    for ip in ips:
        if _ip_is_blocked(ip):
            raise EgressBlocked(f"blocked address: {host} -> {ip}")


async def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Stream the body, stopping at ``max_bytes``; second value flags truncation."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            truncated = True
            break
    body = b"".join(chunks)
    if truncated:
        body = body[:max_bytes]
    return body, truncated


async def guarded_get(client: httpx.AsyncClient, url: str, cfg: WebToolsConfig) -> GuardedResponse:
    """GET ``url`` with the full envelope: scheme + SSRF per hop, manual redirects, byte cap.

    ``client`` must be constructed with ``follow_redirects=False`` — redirects are
    walked here so the guard re-runs on each hop. Raises :class:`EgressBlocked`
    for a refused or over-long-redirecting URL; propagates ``httpx`` errors for
    the caller to map.
    """
    current = url
    for _hop in range(cfg.max_redirects + 1):
        await check_url(current, cfg)
        request = client.build_request("GET", current)
        response = await client.send(request, stream=True)
        try:
            # has_redirect_location is a 3xx *with* a Location header — the only
            # kind we follow. Other 3xx (304 Not Modified, 300 Multiple Choices)
            # are terminal responses and fall through to be read and returned,
            # exactly like any other status (an HTTP status is not a tool error).
            if response.has_redirect_location:
                location = response.headers["location"]
                current = str(response.url.join(location))
                continue
            body, truncated = await _read_capped(response, cfg.fetch_max_bytes)
            return GuardedResponse(
                final_url=str(response.url),
                status_code=response.status_code,
                headers=response.headers,
                content=body,
                truncated=truncated,
                encoding=response.encoding,
            )
        finally:
            await response.aclose()

    raise EgressBlocked(f"too many redirects (> {cfg.max_redirects})")
