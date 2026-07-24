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

::: arcana.observability.events.MemoryPruneEvent

::: arcana.observability.events.MemoryDegradedEvent

::: arcana.observability.events.GuardrailViolationEvent

## Emitters

`emit_degraded` records a memory tier degradation to both the audit log and
metrics. It is the default sink the resilience layer uses when a tier is skipped
or drops out of an operation; the emission is best-effort and never raises, so
observability can never break the memory path.

::: arcana.observability.emit_degraded

`emit_guardrail_violation` records a guardrail match — a blocked call or a
`warn`/`log` one that was allowed through. Enforcement has already happened by
the time it runs, so it is best-effort in the same way. The event deliberately
omits the call's arguments and carries only the target path: a `write_file`
violation must not spill the file's contents into the audit log.

::: arcana.observability.emit_guardrail_violation

## Metrics

Alongside the session and model-call instruments, the memory federation
publishes its resilience signals here: `arcana.memory.tier.degraded` (a counter
labelled by tier / operation / reason), `arcana.memory.tier.latency_ms`,
`arcana.memory.circuit.state` (`0=closed`, `1=half_open`, `2=open` per tier),
and the background-queue gauges `arcana.memory.queue.depth` and
`arcana.memory.queue.drain_seconds`.

::: arcana.observability.metrics.ArcanaMetrics

::: arcana.observability.metrics.get_metrics
