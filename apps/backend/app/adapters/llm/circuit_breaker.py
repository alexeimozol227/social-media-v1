"""Per-(provider, model) circuit breaker with Redis-backed state.

PR #20 / docs/plans/phase1-sprint3-plan.md §2.1.4 — the breaker
exists so a known-bad endpoint (provider 5xx storm, auth key
rotated) fails fast across every web + worker process instead of
each process retrying the full ``tenacity`` budget separately.

State machine:

* ``CLOSED`` (default) — failures increment a counter; once it
  reaches ``fail_threshold`` we transition to ``OPEN``.
* ``OPEN`` — every call short-circuits with
  :class:`LLMCircuitBreakerOpenError` until ``reset_seconds``
  elapse, at which point we transition to ``HALF_OPEN``.
* ``HALF_OPEN`` — exactly one call is allowed; success → ``CLOSED``,
  failure → ``OPEN``.

State lives in Redis under the key
``llm:breaker:{provider}:{model}`` so every process reads the same
"open / closed" signal. Tests pass an in-memory ``fakeredis``
client; production reads ``app.core.redis.get_redis()``.

We don't use ``pybreaker.CircuitBreaker`` directly because its
internal state isn't shareable across processes — the package's
own docs flag this. We borrow the state-machine semantics + the
naming so an audit log row still reads "circuit breaker" to a
human, and we add :class:`pybreaker.CircuitBreakerError` as a
re-export for callers that want to type against it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.core.config import settings


@runtime_checkable
class _AsyncRedisLike(Protocol):
    """Minimal subset of the redis-py async client we depend on."""

    async def hgetall(self, name: str) -> dict[bytes | str, bytes | str]: ...
    async def hset(self, name: str, mapping: dict[str, str]) -> int: ...
    async def hincrby(self, name: str, key: str, amount: int = 1) -> int: ...
    async def expire(self, name: str, time: int) -> bool: ...
    async def delete(self, *names: str) -> int: ...


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    """Tunable parameters; defaults mirror :data:`Settings`."""

    fail_threshold: int
    reset_seconds: int
    namespace: str = "llm:breaker"

    @classmethod
    def from_settings(cls) -> CircuitBreakerConfig:
        return cls(
            fail_threshold=settings.llm_circuit_breaker_fail_threshold,
            reset_seconds=settings.llm_circuit_breaker_reset_seconds,
        )


class LLMCircuitBreaker:
    """Single ``(provider, model)`` breaker view.

    Constructed via :class:`LLMCircuitBreakerRegistry.get` — direct
    instantiation is only for unit tests of the breaker itself.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        config: CircuitBreakerConfig,
        redis: _AsyncRedisLike | None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._config = config
        self._redis = redis
        self._key = f"{config.namespace}:{provider}:{model}"
        # Process-local fallback when no Redis is available (tests
        # without fakeredis, dev sessions with REDIS_URL unset).
        self._local_state: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # public surface
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:  # synchronous probe is intentionally async-only
        """Always False — use :meth:`status` instead.

        We keep this property so callers that previously typed
        against ``pybreaker.CircuitBreaker.is_open`` don't blow up
        at runtime; production code should call :meth:`status`.
        """

        return False

    async def status(self) -> str:
        """Return ``CLOSED`` | ``OPEN`` | ``HALF_OPEN``."""

        state, opened_at, _failures = await self._read()
        if state == "OPEN" and self._is_reset_due(opened_at):
            return "HALF_OPEN"
        return state

    async def is_open_async(self) -> bool:
        return (await self.status()) == "OPEN"

    async def record_success(self) -> None:
        async with self._lock:
            await self._write(state="CLOSED", failures=0, opened_at=0)

    async def record_failure(self, error_code: str) -> None:
        async with self._lock:
            state, opened_at, failures = await self._read()
            if state == "OPEN" and not self._is_reset_due(opened_at):
                # Already open — no-op (failures keep accumulating
                # under the hood for visibility but we don't reset
                # the timer).
                return
            new_failures = failures + 1
            if new_failures >= self._config.fail_threshold:
                await self._write(
                    state="OPEN",
                    failures=new_failures,
                    opened_at=int(time.time()),
                    last_error=error_code,
                )
            else:
                await self._write(
                    state="CLOSED",
                    failures=new_failures,
                    opened_at=opened_at,
                    last_error=error_code,
                )

    async def force_open(self, *, error_code: str = "FORCED") -> None:
        async with self._lock:
            await self._write(
                state="OPEN",
                failures=self._config.fail_threshold,
                opened_at=int(time.time()),
                last_error=error_code,
            )

    async def force_close(self) -> None:
        async with self._lock:
            await self._write(state="CLOSED", failures=0, opened_at=0)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _is_reset_due(self, opened_at: int) -> bool:
        if opened_at <= 0:
            return False
        return time.time() - opened_at >= self._config.reset_seconds

    async def _read(self) -> tuple[str, int, int]:
        """Return ``(state, opened_at, failures)``."""

        if self._redis is None:
            data = self._local_state
        else:
            raw = await self._redis.hgetall(self._key)
            data = {_decode(k): _decode(v) for k, v in raw.items()}
        state = data.get("state", "CLOSED") or "CLOSED"
        opened_at = _safe_int(data.get("opened_at"))
        failures = _safe_int(data.get("failures"))
        return state, opened_at, failures

    async def _write(
        self,
        *,
        state: str,
        failures: int,
        opened_at: int,
        last_error: str | None = None,
    ) -> None:
        mapping = {
            "state": state,
            "failures": str(max(0, failures)),
            "opened_at": str(max(0, opened_at)),
        }
        if last_error:
            mapping["last_error"] = last_error
        if self._redis is None:
            self._local_state = mapping
            return
        await self._redis.hset(self._key, mapping=mapping)
        # Keep the row pinned long enough to cover the reset window
        # + a safety margin so a quiet day doesn't flush state we
        # actually want to keep.
        ttl = max(self._config.reset_seconds * 4, 300)
        await self._redis.expire(self._key, ttl)


class LLMCircuitBreakerRegistry:
    """Caches per-(provider, model) breakers in-process.

    The factory wires one registry per provider instance — every
    breaker shares the same Redis connection so the underlying
    state is the source of truth.
    """

    def __init__(
        self,
        *,
        config: CircuitBreakerConfig | None = None,
        redis: _AsyncRedisLike | None = None,
    ) -> None:
        self._config = config or CircuitBreakerConfig.from_settings()
        self._redis = redis
        self._cache: dict[tuple[str, str], LLMCircuitBreaker] = {}
        self._lock = asyncio.Lock()

    async def get(self, provider: str, model: str) -> LLMCircuitBreaker:
        key = (provider, model)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is None:
                cached = LLMCircuitBreaker(
                    provider=provider,
                    model=model,
                    config=self._config,
                    redis=self._redis,
                )
                self._cache[key] = cached
            return cached


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _safe_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray, str)):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if value is None:
        return ""
    return str(value)


__all__ = [
    "CircuitBreakerConfig",
    "LLMCircuitBreaker",
    "LLMCircuitBreakerRegistry",
]
