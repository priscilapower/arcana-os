"""DuckDuckGo provider — the keyless default. No signup required.

Scrapes DuckDuckGo's HTML endpoint and pulls results out of its markup, so
``pip install arcana-os`` yields a working agent with no API key. Best-effort: a
markup change degrades results, never crashes the tool.
"""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx

from arcana.tools.builtins.web.search.base import SearchResult


def _decode_ddg_href(href: str) -> str:
    """Unwrap DuckDuckGo's ``/l/?uddg=<real-url>`` redirect to the target URL."""
    uddg = parse_qs(urlsplit(href).query).get("uddg")
    if uddg:
        return uddg[0]
    if href.startswith("//"):
        return f"https:{href}"
    return href


class _DuckDuckGoResultParser(HTMLParser):
    """Pulls ``{title, url, snippet}`` out of DuckDuckGo's HTML results page.

    DuckDuckGo wraps each hit's link in ``a.result__a`` (with the real URL hidden
    in the ``uddg`` query param of a redirect href) and its summary in
    ``a.result__snippet``.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._in_title = False
        self._in_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._pending_url = ""

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for key, value in attrs:
            if key == "class" and value:
                return set(value.split())
        return set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        classes = self._classes(attrs)
        if "result__a" in classes:
            self._flush()
            self._in_title = True
            self._title_parts = []
            href = next((v for k, v in attrs if k == "href"), None)
            self._pending_url = _decode_ddg_href(href or "")
        elif "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._in_snippet:
            self._in_snippet = False
        elif self._in_title:
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def _flush(self) -> None:
        title = "".join(self._title_parts).strip()
        if not self._pending_url:
            return
        self.results.append(
            SearchResult(
                title=title,
                url=self._pending_url,
                snippet="".join(self._snippet_parts).strip(),
            )
        )
        self._pending_url = ""
        self._snippet_parts = []

    def finish(self) -> list[SearchResult]:
        self._flush()
        return self.results


class DuckDuckGoProvider:
    """Keyless default: scrapes DuckDuckGo's HTML endpoint. No signup required."""

    name = "duckduckgo"

    def __init__(self, client: httpx.AsyncClient, url: str, user_agent: str) -> None:
        self._client = client
        self._url = url
        self._user_agent = user_agent

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        response = await self._client.post(
            self._url,
            data={"q": query},
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
        )
        response.raise_for_status()
        parser = _DuckDuckGoResultParser()
        parser.feed(response.text)
        parser.close()
        return parser.finish()[:max_results]
