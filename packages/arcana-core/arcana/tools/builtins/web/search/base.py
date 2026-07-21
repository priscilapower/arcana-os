"""The swappable ``web_search`` seam: normalised result shape + provider protocol.

Search APIs differ in auth, shape, and availability, so ``web_search`` talks to a
:class:`SearchProvider` rather than any one API. Every provider normalises to
``[{title, url, snippet}]``; provider/network errors propagate to the handler,
which maps them to a ``ToolResult`` error the model can act on.
"""

from typing import Protocol, TypedDict


class SearchResult(TypedDict):
    """One normalised search hit."""

    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    """A web-search backend. ``name`` is a non-sensitive label for observability."""

    name: str

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]: ...
