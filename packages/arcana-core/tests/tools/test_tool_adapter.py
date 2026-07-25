"""Tests for the ToolAdapter ABC and the builtin adapter's handler table."""

from typing import Any

from arcana.tools.adapters.base import BuiltinToolAdapter
from arcana.tools.builtins.definitions import BUILTIN_DEFINITIONS
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.builtins.web.search import SearchResult
from arcana.types.tool import ToolType


class _FakeProvider:
    """A SearchProvider stand-in returning canned results (no network)."""

    name = "fake"

    def __init__(self, results: list[SearchResult] | None = None, *, raises: Exception | None = None) -> None:
        self._results = results or []
        self._raises = raises
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        self.calls.append((query, max_results))
        if self._raises is not None:
            raise self._raises
        return self._results[:max_results]


def _adapter(provider: _FakeProvider | None = None, **cfg: Any) -> BuiltinToolAdapter:
    return BuiltinToolAdapter(WebToolsConfig(**cfg), provider=provider or _FakeProvider())


# ---------------------------------------------------------------------------
# Handler table shape
# ---------------------------------------------------------------------------


async def test_provides_returns_every_hosted_builtin():
    adapter = _adapter()
    assert [d.name for d in adapter.provides()] == [
        "web_search",
        "fetch_url",
        "list_dir",
        "read_file",
        "write_file",
        "delete_file",
        "make_dir",
        "move",
        "copy",
        "delete_dir",
    ]
    assert adapter.type == ToolType.BUILTIN
    await adapter.aclose()


async def test_provides_returns_the_shared_definition_objects():
    # Same object identity as the single source of truth — no drift possible.
    adapter = _adapter()
    provided = {d.name: d for d in adapter.provides()}
    assert provided["web_search"] is BUILTIN_DEFINITIONS["web_search"]
    assert provided["fetch_url"] is BUILTIN_DEFINITIONS["fetch_url"]
    await adapter.aclose()


async def test_supports_matches_provided_definitions():
    adapter = _adapter()
    assert adapter.supports("web_search") is True
    assert adapter.supports("fetch_url") is True
    assert adapter.supports("echo") is False
    await adapter.aclose()


async def test_execute_unknown_builtin_returns_failure():
    adapter = _adapter()
    result = await adapter.execute("nope", {})
    assert result.success is False
    assert result.error is not None and "nope" in result.error
    await adapter.aclose()


async def test_handler_exception_is_caught_not_raised():
    # A provider that raises must surface as a failed result, never an exception.
    adapter = _adapter(_FakeProvider(raises=RuntimeError("kaboom")))
    result = await adapter.execute("web_search", {"query": "x"})
    assert result.success is False
    assert result.error is not None and "kaboom" in result.error
    await adapter.aclose()


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


async def test_web_search_normalises_provider_results():
    provider = _FakeProvider([SearchResult(title="T", url="https://e.com", snippet="S")])
    adapter = _adapter(provider)
    result = await adapter.execute("web_search", {"query": "cats"})
    assert result.success is True
    assert result.output == [{"title": "T", "url": "https://e.com", "snippet": "S"}]
    await adapter.aclose()


async def test_web_search_empty_results_is_success():
    adapter = _adapter(_FakeProvider([]))
    result = await adapter.execute("web_search", {"query": "cats"})
    assert result.success is True
    assert result.output == []
    await adapter.aclose()


async def test_web_search_missing_query_fails():
    adapter = _adapter()
    result = await adapter.execute("web_search", {})
    assert result.success is False
    assert result.error is not None and "query" in result.error
    await adapter.aclose()


async def test_web_search_clamps_max_results_to_ceiling():
    provider = _FakeProvider([])
    adapter = _adapter(provider, web_search_max_results=3)
    await adapter.execute("web_search", {"query": "cats", "max_results": 9})
    assert provider.calls == [("cats", 3)]  # 9 clamped down to the ceiling
    await adapter.aclose()


async def test_web_search_defaults_max_results_when_absent():
    provider = _FakeProvider([])
    adapter = _adapter(provider, web_search_max_results=5)
    await adapter.execute("web_search", {"query": "cats"})
    assert provider.calls == [("cats", 5)]
    await adapter.aclose()
