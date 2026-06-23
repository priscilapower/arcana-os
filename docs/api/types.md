# Types

All Pydantic models are re-exported from `arcana.types`. Import from there,
not from sub-modules, so your code is insulated from internal restructuring.

```python
from arcana.types import Agent, ModelConnection, Session
```

## Agent record

::: arcana.types.agent.Agent

::: arcana.types.agent.AgentStatus

## Model connection

::: arcana.types.model.ModelConnection

::: arcana.types.model.ModelProvider

::: arcana.types.model.ModelCapabilities

::: arcana.types.model.ModelTransport

## Session

::: arcana.types.session.Session

::: arcana.types.session.Message

::: arcana.types.session.MessageRole

::: arcana.types.session.SessionStatus

## Memory

::: arcana.types.memory.MemoryEntry

::: arcana.types.memory.MemoryQuery

::: arcana.types.memory.MemoryScope

::: arcana.types.memory.MemoryType

::: arcana.types.memory.MemoryAdapter

::: arcana.types.memory.AdapterCapabilities

::: arcana.types.memory.AdapterHealth
