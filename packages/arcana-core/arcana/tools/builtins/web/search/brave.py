"""Brave provider — keyed via the Brave Search API (``BRAVE_API_KEY``)."""

import httpx
from pydantic import BaseModel, Field

from arcana.tools.builtins.web.search.base import SearchResult


class _BraveResult(BaseModel):
    title: str = ""
    url: str = ""
    description: str = ""


class _BraveWeb(BaseModel):
    results: list[_BraveResult] = []


class _BraveResponse(BaseModel):
    """The subset of the Brave payload we use; unknown fields are ignored."""

    web: _BraveWeb = Field(default_factory=_BraveWeb)


class BraveProvider:
    """Keyed provider using the Brave Search API."""

    name = "brave"

    def __init__(self, client: httpx.AsyncClient, api_key: str, url: str) -> None:
        self._client = client
        self._api_key = api_key
        self._url = url

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        response = await self._client.get(
            self._url,
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = _BraveResponse.model_validate(response.json())
        return [
            SearchResult(title=r.title, url=r.url, snippet=r.description) for r in payload.web.results[:max_results]
        ]
