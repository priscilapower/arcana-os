# Observability

`arcana.observability` provides a structured audit log, OpenTelemetry tracing,
and in-process metrics. Everything writes to `~/.arcana/logs/` by default.

```python
from arcana.observability import configure_observability, get_audit_log

configure_observability()        # call once at startup
log = get_audit_log()
for event in log.tail(n=20):
    print(event)
```

Install the OTel extras for span export to Jaeger, Grafana, etc.:

```
pip install arcana-core[observability]
```

## Configuration

::: arcana.observability.configure_observability

::: arcana.observability.get_audit_log

::: arcana.observability.tracer.configure_tracing

::: arcana.observability.tracer.get_tracer

## Audit log

::: arcana.observability.audit.AuditLog

## Events

::: arcana.observability.events.AuditEvent

::: arcana.observability.events.SessionEvent

::: arcana.observability.events.ModelCallEvent

::: arcana.observability.events.RoutingEvent

::: arcana.observability.events.MemoryReadEvent

::: arcana.observability.events.MemoryWriteEvent

## Metrics

::: arcana.observability.metrics.ArcanaMetrics

::: arcana.observability.metrics.get_metrics
