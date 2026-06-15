# API reference

The `arcana` package (from `arcana-core`) is the library you build agents with.
These pages are generated directly from the source docstrings via
[mkdocstrings](https://mkdocstrings.github.io/), so they stay in lockstep with
the code.

## Top-level exports

```python
from arcana import Agent, Card, CardRegistry, AgentRegistry, SessionManager
from arcana.models import ConnectionStore, ModelGateway
```

| Symbol | Page |
|--------|------|
| `Agent`, `AgentRegistry`, `SessionManager` | [Agent](agent.md) |
| `Card`, `CardRegistry` | [Cards](cards.md) |
| `ModelGateway`, `ConnectionStore`, adapters | [Models](models.md) |
