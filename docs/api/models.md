# Models

The model gateway routes completion requests to the right provider adapter,
handles retries, and tracks usage and cost.

## Gateway

::: arcana.models.gateway.ModelGateway

::: arcana.models.gateway.RetryPolicy

::: arcana.models.gateway.ProviderRegistry

::: arcana.models.gateway.ProviderEntry

## Connection store

::: arcana.models.connection_store.ConnectionStore

## Adapters

::: arcana.models.adapters.base.ModelAdapter

::: arcana.models.adapters.base.CompletionRequest

::: arcana.models.adapters.base.CompletionResponse

::: arcana.models.adapters.base.MessageParam

::: arcana.models.adapters.base.ModelChunk

::: arcana.models.adapters.base.ModelHealth

## Embedding adapters

Embedding generation is separate from both vector storage and the completion
`ModelAdapter` above: an `EmbeddingAdapter` turns text into a dense vector.
Concrete adapters target one provider each.

- `OllamaEmbeddingAdapter` — Tier 1, `nomic-embed-text` (768d) via Ollama's
  `/api/embed`; health probed through `/api/tags`.
- `FastEmbedEmbeddingAdapter` — Tier 2, `nomic-embed-text-v1.5` (768d) via
  fastembed's in-process ONNX runtime. Optional dependency
  (`arcana-core[embed]`), imported lazily — `health_check()` reports unhealthy
  when the package is absent rather than failing at import.

Both report a shared `model_family`, so a database pinned to one can fall back to
the other when its vectors are interchangeable. See
[model pinning](memory.md#embedding-gateway-and-model-pinning).

::: arcana.models.adapters.embedding.EmbeddingAdapter

::: arcana.models.adapters.embedding.EmbeddingError

::: arcana.models.adapters.ollama_embedding.OllamaEmbeddingAdapter

::: arcana.models.adapters.fastembed_embedding.FastEmbedEmbeddingAdapter

## Pricing

::: arcana.models.pricing.Usage

::: arcana.models.pricing.CostEvent

::: arcana.models.pricing.PricingTable

## Errors

::: arcana.models.errors.ModelError

::: arcana.models.errors.ModelTransientError

::: arcana.models.errors.ModelUnavailableError

::: arcana.models.errors.ModelAuthError

::: arcana.models.errors.ModelBadRequestError

::: arcana.models.errors.ModelNotFoundError

::: arcana.models.errors.ModelNotConfiguredError
