"""Tests for OllamaEmbeddingAdapter. No live Ollama — the HTTP client is mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from arcana.models.adapters.ollama_embedding import EmbeddingError, OllamaEmbeddingAdapter
from arcana.types import AdapterHealth


def _resp(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://localhost:11434/api/embed")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(status, request=req))


@pytest.fixture
def mock_http():
    client = MagicMock()
    client.post = AsyncMock()
    client.get = AsyncMock()
    return client


@pytest.fixture
def adapter(mock_http):
    with patch("arcana.models.adapters.ollama_embedding.httpx.AsyncClient", return_value=mock_http):
        return OllamaEmbeddingAdapter(model="nomic-embed-text", dimensions=3)


async def test_model_name_and_dimensions(adapter) -> None:
    assert adapter.model_name == "nomic-embed-text"
    assert adapter.dimensions == 3


async def test_embed_returns_single_vector(adapter, mock_http) -> None:
    mock_http.post.return_value = _resp({"embeddings": [[0.1, 0.2, 0.3]]})

    vec = await adapter.embed("hello")

    assert vec == [0.1, 0.2, 0.3]
    payload = mock_http.post.call_args.kwargs["json"]
    assert payload["input"] == "hello"  # single string, not wrapped in a list
    assert payload["model"] == "nomic-embed-text"
    assert mock_http.post.call_args.args[0].endswith("/api/embed")


async def test_embed_batch_is_one_native_request(adapter, mock_http) -> None:
    mock_http.post.return_value = _resp({"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]})

    out = await adapter.embed_batch(["a", "b"])

    assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    mock_http.post.assert_awaited_once()  # batched, not one call per text
    assert mock_http.post.call_args.kwargs["json"]["input"] == ["a", "b"]


async def test_embed_batch_empty_short_circuits(adapter, mock_http) -> None:
    assert await adapter.embed_batch([]) == []
    mock_http.post.assert_not_awaited()


async def test_dimension_mismatch_raises(adapter, mock_http) -> None:
    mock_http.post.return_value = _resp({"embeddings": [[0.1, 0.2, 0.3, 0.4]]})  # 4 != 3
    with pytest.raises(EmbeddingError, match="dim"):
        await adapter.embed("hello")


async def test_empty_embeddings_raises(adapter, mock_http) -> None:
    mock_http.post.return_value = _resp({"embeddings": []})
    with pytest.raises(EmbeddingError, match="no embedding"):
        await adapter.embed("hello")


async def test_connect_error_becomes_embedding_error(adapter, mock_http) -> None:
    mock_http.post.side_effect = httpx.ConnectError("refused")
    with pytest.raises(EmbeddingError, match="Is Ollama running"):
        await adapter.embed("hello")


async def test_model_not_found_message(adapter, mock_http) -> None:
    mock_http.post.side_effect = _http_error(404)
    with pytest.raises(EmbeddingError, match="ollama pull"):
        await adapter.embed("hello")


async def test_health_check_healthy_when_model_present(adapter, mock_http) -> None:
    mock_http.get.return_value = _resp({"models": [{"name": "nomic-embed-text:latest"}]})

    health = await adapter.health_check()

    assert isinstance(health, AdapterHealth)
    assert health.healthy is True
    assert health.adapter_id == "nomic-embed-text"


async def test_health_check_unhealthy_when_model_absent(adapter, mock_http) -> None:
    mock_http.get.return_value = _resp({"models": [{"name": "llama3"}]})

    health = await adapter.health_check()

    assert health.healthy is False
    assert "not pulled" in health.message


async def test_health_check_never_raises(adapter, mock_http) -> None:
    mock_http.get.side_effect = httpx.ConnectError("refused")

    health = await adapter.health_check()

    assert health.healthy is False
