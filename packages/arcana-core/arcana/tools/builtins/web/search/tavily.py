"""Tavily provider — keyed via the Tavily Search API (``TAVILY_API_KEY``)."""

import httpx
from pydantic import BaseModel

from arcana.tools.builtins.web.search.base import SearchResult


class _TavilyResult(BaseModel):
    title: str = ""
    url: str = ""
    content: str = ""


class _TavilyResponse(BaseModel):
    """The subset of the Tavily payload we use; unknown fields are ignored."""

    results: list[_TavilyResult] = []


class TavilyProvider:
    """Keyed provider using the Tavily Search API."""

    name = "tavily"

    def __init__(self, client: httpx.AsyncClient, api_key: str, url: str) -> None:
        self._client = client
        self._api_key = api_key
        self._url = url

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        response = await self._client.post(
            self._url,
            json={"api_key": self._api_key, "query": query, "max_results": max_results},
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = _TavilyResponse.model_validate(response.json())
        return [SearchResult(title=r.title, url=r.url, snippet=r.content) for r in payload.results[:max_results]]
