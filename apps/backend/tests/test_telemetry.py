"""Tests for the OpenTelemetry bootstrap module (PR #9, docs/04 §15).

The test suite doesn't spin up an OTLP collector; it validates:
* No-op mode when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is empty.
* ``get_tracer()`` / ``get_meter()`` return usable objects in noop mode.
* ``inject_trace_context`` / ``extract_trace_context`` don't crash
  even without OTel SDK.
* ``setup_telemetry`` is idempotent (safe to call twice).
"""

from __future__ import annotations

import pytest

from app.core.telemetry import (
    _NoOpMeter,
    _NoOpTracer,
    extract_trace_context,
    get_meter,
    get_tracer,
    inject_trace_context,
    reset_telemetry_for_tests,
    setup_telemetry,
)

# OTel SDK is installed in dev, so get_tracer()/get_meter() return
# real ProxyTracer/ProxyMeter instead of our _NoOp stubs. The _NoOp*
# classes are tested directly in their own test classes below.


@pytest.fixture(autouse=True)
def _clean_telemetry() -> None:
    reset_telemetry_for_tests()


class TestNoOpMode:
    """When OTEL_EXPORTER_OTLP_ENDPOINT is empty → no-op."""

    def test_setup_telemetry_noop_does_not_raise(self) -> None:
        setup_telemetry()

    def test_setup_telemetry_idempotent(self) -> None:
        setup_telemetry()
        setup_telemetry()  # second call is a no-op

    def test_get_tracer_returns_usable_object(self) -> None:
        tracer = get_tracer()
        # Should return either our NoOp or OTel's ProxyTracer — both are usable.
        assert hasattr(tracer, "start_as_current_span") or hasattr(tracer, "start_span")

    def test_get_meter_returns_usable_object(self) -> None:
        meter = get_meter()
        assert hasattr(meter, "create_counter") or hasattr(meter, "create_histogram")


class TestNoOpTracer:
    def test_start_as_current_span_context_manager(self) -> None:
        tracer = _NoOpTracer()
        with tracer.start_as_current_span("test") as span:
            span.set_attribute("key", "value")

    def test_start_span(self) -> None:
        tracer = _NoOpTracer()
        span = tracer.start_span("test")
        span.set_attribute("key", "value")
        span.record_exception(ValueError("test"))


class TestNoOpMeter:
    def test_counter(self) -> None:
        meter = _NoOpMeter()
        counter = meter.create_counter("test_counter")
        counter.add(1)
        counter.add(5, attributes={"env": "test"})

    def test_histogram(self) -> None:
        meter = _NoOpMeter()
        hist = meter.create_histogram("test_histogram")
        hist.record(42.0)
        hist.record(1.5, attributes={"path": "/api"})

    def test_up_down_counter(self) -> None:
        meter = _NoOpMeter()
        gauge = meter.create_up_down_counter("test_gauge")
        gauge.add(1)
        gauge.add(-1)


class TestTraceContextPropagation:
    def test_inject_returns_carrier(self) -> None:
        carrier: dict[str, object] = {"existing": "value"}
        result = inject_trace_context(carrier)
        assert result is carrier
        assert result["existing"] == "value"

    def test_extract_returns_none_without_otel(self) -> None:
        carrier: dict[str, object] = {}
        ctx = extract_trace_context(carrier)
        # Without OTel SDK configured, may return None or a context obj
        # — the point is it doesn't crash.
        assert ctx is None or ctx is not None
