"""LLM provider adapters.

PR #20 / docs/plans/phase1-sprint3-plan.md — every agent talks to
a single :class:`LLMProvider` Protocol; the concrete implementation
is selected via :data:`app.core.config.Settings.llm_provider` so
tests run against :class:`MockLLMProvider` (deterministic,
network-free) and production talks to Polza.
"""

from app.adapters.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMBudgetExceededError,
    LLMCircuitBreakerOpenError,
    LLMContentFilterBlockedError,
    LLMContextLengthError,
    LLMError,
    LLMProvider,
    LLMProviderError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMTimeoutError,
    ProviderHealth,
    ResponseFormat,
    ToolCall,
    ToolSpec,
    Usage,
)
from app.adapters.llm.circuit_breaker import (
    CircuitBreakerConfig,
    LLMCircuitBreaker,
    LLMCircuitBreakerRegistry,
)
from app.adapters.llm.factory import build_default_provider
from app.adapters.llm.mock import MockLLMProvider
from app.adapters.llm.polza import PolzaProvider, PolzaResponseCache
from app.adapters.llm.pricing import (
    ModelPricing,
    UnknownModelPricingError,
    all_pricings,
    compute_cost_usd,
    get_pricing,
)

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "CircuitBreakerConfig",
    "LLMBudgetExceededError",
    "LLMCircuitBreaker",
    "LLMCircuitBreakerOpenError",
    "LLMCircuitBreakerRegistry",
    "LLMContentFilterBlockedError",
    "LLMContextLengthError",
    "LLMError",
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderUnavailableError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "MockLLMProvider",
    "ModelPricing",
    "PolzaProvider",
    "PolzaResponseCache",
    "ProviderHealth",
    "ResponseFormat",
    "ToolCall",
    "ToolSpec",
    "UnknownModelPricingError",
    "Usage",
    "all_pricings",
    "build_default_provider",
    "compute_cost_usd",
    "get_pricing",
]
