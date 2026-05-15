"""OpenTelemetry bootstrap (docs/04 §15, docs/05 §10.2).

Initialises tracing + metrics instrumentations for:
* **FastAPI** — auto-instruments every HTTP request with spans.
* **SQLAlchemy** — auto-instruments every DB query.
* **Redis** — auto-instruments every Redis command.
* **Celery** — auto-instruments task enqueue / execute (when Celery
  is added in a follow-up sprint).

When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset (local dev / CI) the
module configures a no-op tracer so application code can always call
``get_tracer()`` / ``get_meter()`` without branching.

Trace propagation
-----------------
Each event on the Redis Pub/Sub bus carries a ``trace_id`` field
(docs/05 §10.2). The helper :func:`inject_trace_context` /
:func:`extract_trace_context` serialize the W3C ``traceparent``
header into / out of event dicts so a consumer span becomes a child
of the producer span.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy globals — populated by ``setup_telemetry()``
# ---------------------------------------------------------------------------

_tracer: Any = None
_meter: Any = None
_initialized: bool = False


def setup_telemetry() -> None:
    """One-shot OTel bootstrap.  Call once during app lifespan startup."""

    global _tracer, _meter, _initialized

    if _initialized:
        return

    if not settings.otel_exporter_otlp_endpoint:
        logger.info(
            "telemetry.noop",
            hint="Set OTEL_EXPORTER_OTLP_ENDPOINT to enable real tracing",
        )
        _initialized = True
        return

    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import (
            FastAPIInstrumentor,
        )
        from opentelemetry.instrumentation.redis import (
            RedisInstrumentor,
        )
        from opentelemetry.instrumentation.sqlalchemy import (
            SQLAlchemyInstrumentor,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
        )
    except ImportError:
        logger.warning(
            "telemetry.import_failed",
            hint="pip install opentelemetry-sdk opentelemetry-instrumentation-fastapi ...",
        )
        _initialized = True
        return

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.0.1",
            "deployment.environment": settings.environment,
        }
    )

    # ---- Traces ----
    span_exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=not settings.otel_exporter_otlp_endpoint.startswith("https"),
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)
    _tracer = trace.get_tracer(__name__)

    # ---- Metrics ----
    metric_exporter = OTLPMetricExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=not settings.otel_exporter_otlp_endpoint.startswith("https"),
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(metric_exporter)],
    )
    otel_metrics.set_meter_provider(meter_provider)
    _meter = otel_metrics.get_meter(__name__)

    # ---- Auto-instrumentations ----
    FastAPIInstrumentor.instrument()
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()

    # Celery instrumentation is wired separately when the Celery
    # worker process boots (follow-up PR).

    logger.info(
        "telemetry.initialized",
        endpoint=settings.otel_exporter_otlp_endpoint,
        service=settings.otel_service_name,
    )
    _initialized = True


def shutdown_telemetry() -> None:
    """Flush pending spans / metrics on shutdown."""

    if not settings.otel_exporter_otlp_endpoint:
        return

    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()

        meter_provider = otel_metrics.get_meter_provider()
        if hasattr(meter_provider, "shutdown"):
            meter_provider.shutdown()
    except Exception:
        logger.warning("telemetry.shutdown_error", exc_info=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_tracer() -> Any:
    """Return the app-wide tracer (may be a no-op proxy)."""

    if _tracer is not None:
        return _tracer

    try:
        from opentelemetry import trace

        return trace.get_tracer(__name__)
    except ImportError:
        return _NoOpTracer()


def get_meter() -> Any:
    """Return the app-wide meter (may be a no-op proxy)."""

    if _meter is not None:
        return _meter

    try:
        from opentelemetry import metrics as otel_metrics

        return otel_metrics.get_meter(__name__)
    except ImportError:
        return _NoOpMeter()


# ---------------------------------------------------------------------------
# Trace-context propagation helpers (docs/05 §10.2)
# ---------------------------------------------------------------------------


def inject_trace_context(carrier: dict[str, Any]) -> dict[str, Any]:
    """Inject the current W3C ``traceparent`` into *carrier* dict.

    Used by the event-bus publisher so each event carries the trace
    context for downstream consumers.
    """

    try:
        from opentelemetry.propagate import inject

        inject(carrier)
    except ImportError:
        pass
    return carrier


def extract_trace_context(carrier: dict[str, Any]) -> Any:
    """Extract trace context from *carrier* and return a token.

    Used by event-bus consumers to create child spans linked to the
    producer's trace.
    """

    try:
        from opentelemetry.propagate import extract

        return extract(carrier)
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# No-op stubs (when OTel SDK is not installed)
# ---------------------------------------------------------------------------


class _NoOpSpan:
    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass


class _NoOpTracer:
    def start_as_current_span(self, name: str, **kwargs: Any) -> _NoOpSpan:
        return _NoOpSpan()

    def start_span(self, name: str, **kwargs: Any) -> _NoOpSpan:
        return _NoOpSpan()


class _NoOpMeter:
    def create_counter(self, name: str, **kwargs: Any) -> _NoOpCounter:
        return _NoOpCounter()

    def create_histogram(self, name: str, **kwargs: Any) -> _NoOpHistogram:
        return _NoOpHistogram()

    def create_up_down_counter(self, name: str, **kwargs: Any) -> _NoOpCounter:
        return _NoOpCounter()


class _NoOpCounter:
    def add(self, amount: int | float, attributes: dict[str, Any] | None = None) -> None:
        pass


class _NoOpHistogram:
    def record(self, amount: int | float, attributes: dict[str, Any] | None = None) -> None:
        pass


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def reset_telemetry_for_tests() -> None:
    """Reset the module-level state (tests only)."""

    global _tracer, _meter, _initialized
    _tracer = None
    _meter = None
    _initialized = False
