# API reference

The `arcana` package (from `arcana-core`) is the library you build agents with.
These pages are generated directly from the source docstrings via
[mkdocstrings](https://mkdocstrings.github.io/), so they stay in lockstep with
the code.

## Top-level imports

```python
from arcana import Agent, AgentRegistry, Card, CardRegistry, SessionManager
from arcana.models import ModelGateway, ConnectionStore
from arcana.evals import EvalHarness, EvalCase
from arcana.observability import configure_observability, get_audit_log
```

## Module map

| Module | What it contains | Page |
|--------|-----------------|------|
| `arcana.agents` | `Agent`, `AgentRegistry`, `SessionManager` | [Agent](agent.md) |
| `arcana.cards` | `Card`, `CardRegistry`, `CardEngine` | [Cards](cards.md) |
| `arcana.types` | Pydantic models — `ModelConnection`, `AgentRecord`, `Session`, … | [Types](types.md) |
| `arcana.models` | `ModelGateway`, `ConnectionStore`, adapters, errors, pricing | [Models](models.md) |
| `arcana.evals` | `EvalHarness`, judges, `EvalCase` | [Evals](evals.md) |
| `arcana.observability` | `AuditLog`, events, metrics, tracing | [Observability](observability.md) |
