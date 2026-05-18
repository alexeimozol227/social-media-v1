"""``HealthCheckAgent`` — first concrete agent (PR #20).

docs/plans/phase1-sprint3-plan.md §"``HealthCheckAgent``": the
minimal agent in this PR so the whole pipeline (``BaseAgent`` +
:class:`LLMProvider` + :class:`AgentRunWriter`) can be exercised
end-to-end. Sends one tiny "reply OK" prompt to the configured
LLM provider, records the call, and returns a typed result.

Powers the ``POST /v1/admin/healthcheck/llm`` endpoint + the
``/admin/llm-healthcheck`` SSR page.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, ClassVar

from app.adapters.llm.base import (
    ChatMessage,
    LLMCircuitBreakerOpenError,
    LLMError,
)
from app.agents.base import (
    AgentContext,
    BaseAgent,
    _RunBookkeeping,
)
from app.models.llm_call import CircuitBreakerState, LLMCallType
from app.services.agent_run_writer import LLMUsage, hash_prompt

_HEALTHCHECK_PROMPT = "Reply with the word OK."
_HEALTHCHECK_MAX_TOKENS = 10


class HealthCheckAgent(BaseAgent):
    """Round-trip the LLM gateway and write one ``llm_calls`` row."""

    agent_name: ClassVar[str] = "healthcheck"
    agent_version: ClassVar[str] = "v0"

    def __init__(
        self,
        *,
        llm_provider: Any,
        audit_writer: Any,
        model: str = "gpt-4o-mini",
    ) -> None:
        super().__init__(llm_provider=llm_provider, audit_writer=audit_writer)
        self._model = model

    async def run(
        self,
        context: AgentContext,
        *,
        run_id: uuid.UUID,
        bookkeeping: _RunBookkeeping,
    ) -> dict[str, Any] | None:
        del context  # healthcheck doesn't need any agent-specific input
        prompt = _HEALTHCHECK_PROMPT
        prompt_h = hash_prompt(prompt)

        breaker_state = CircuitBreakerState.CLOSED
        success = True
        error_code: str | None = None
        response_id: str | None = None
        raw_output: str | None = None
        prompt_tokens = 0
        completion_tokens = 0
        captured_error: LLMError | None = None

        started = time.monotonic()
        try:
            chat_response = await self._llm_provider.chat(
                messages=[ChatMessage(role="user", content=prompt)],
                model=self._model,
                max_tokens=_HEALTHCHECK_MAX_TOKENS,
                temperature=0.0,
            )
            raw_output = chat_response.content
            response_id = chat_response.response_id
            prompt_tokens = chat_response.usage.prompt_tokens
            completion_tokens = chat_response.usage.completion_tokens
            bookkeeping.chain_of_thought.append(
                {
                    "step": "healthcheck.chat",
                    "model": self._model,
                    "prompt_hash": prompt_h,
                    "finish_reason": chat_response.finish_reason,
                },
            )
        except LLMCircuitBreakerOpenError as exc:
            success = False
            error_code = exc.error_code
            breaker_state = CircuitBreakerState.OPEN
            raw_output = None
            captured_error = exc
        except LLMError as exc:
            success = False
            error_code = exc.error_code
            raw_output = None
            captured_error = exc
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)

        await self._audit_writer.record_llm_call(
            run_id,
            provider=getattr(self._llm_provider, "provider_slug", "unknown"),
            model=self._model,
            call_type=LLMCallType.CHAT,
            prompt_hash=prompt_h,
            prompt_full=prompt,
            tools_called=[],
            raw_output=raw_output,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            latency_ms=latency_ms,
            circuit_breaker_state=breaker_state,
            retries=0,
            success=success,
            error_code=error_code,
            response_id=response_id,
        )

        if captured_error is not None:
            # Re-raise the original typed error so BaseAgent.invoke
            # preserves the specific ``error_code`` on the agent_run row.
            raise captured_error
        return None


__all__ = ["HealthCheckAgent"]
