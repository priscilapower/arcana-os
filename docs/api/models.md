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
