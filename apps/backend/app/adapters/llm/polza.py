"""Polza (https://polza.ai/) LLM provider — skeleton (PR #17).

The real network wiring — request payload, retry / cost tracking,
response parsing — lands in Sprint 3 alongside the first real
agent that consumes :meth:`LLMProvider.complete`. PR #17 reserves:

* the import surface (:class:`PolzaProvider` is importable from
  :mod:`app.adapters.llm` so factory wiring + lint configs are
  finalised today);
* the config plumbing (:data:`app.core.config.Settings.polza_api_key`
  / ``polza_base_url``);
* the :func:`build_default_provider` factory that the Celery task /
  agents use to look up the active provider without hard-coding a
  class.

Calling :meth:`PolzaProvider.complete` or :meth:`PolzaProvider.embed`
before Sprint 3 raises :class:`NotImplementedError` so a mis-configured
production deploy (``LLM_PROVIDER=polza`` without the rest of Sprint 3)
fails fast and loudly rather than silently dropping prompts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from app.adapters.llm.base import (
    EmbeddingResult,
    LLMProvider,
    LLMResult,
    Tool,
)
from app.core.config import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.adapters.llm.mock import MockLLMProvider


# Default timeout for outbound Polza HTTP calls. Embedding endpoints
# return sub-second under load; completion endpoints can stream for
# 60+ seconds. We pick a 30 s middle-of-the-road default — Sprint 3
# splits it into per-endpoint timeouts.
_POLZA_TIMEOUT_SECONDS = 30.0


class PolzaProvider(LLMProvider):
    """Skeleton client for Polza LLM gateway.

    The constructor wires an :class:`httpx.AsyncClient` with the
    correct base URL + auth header so the eventual Sprint 3 wire-up
    is a one-method-body change. Until then the request methods
    raise :class:`NotImplementedError` with a clear "this lands in
    Sprint 3" message so a production deploy with the wrong env
    fails fast.

    The HTTP client is created eagerly so a misconfiguration (e.g.
    empty ``polza_base_url``) surfaces at startup rather than on
    the first request — the agent layer assumes provider
    construction is cheap and side-effect-free.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.polza.ai/api/v1",
        timeout: float = _POLZA_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            # Defensive — the factory below is the only sanctioned
            # construction path and already handles the empty case,
            # but a direct caller (test, custom worker) shouldn't be
            # able to silently produce an unauthenticated client.
            raise ValueError("PolzaProvider requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "social-media-v1/backend",
            },
        )

    @property
    def base_url(self) -> str:
        """The configured Polza endpoint (no trailing slash)."""

        return self._base_url

    def __repr__(self) -> str:
        # Deliberately don't include the api_key — repr() lands in
        # log lines + structlog frames and we don't want the bearer
        # token to leak. The suffix gives operators enough to
        # correlate without exposing the secret.
        suffix = f"...{self._api_key[-4:]}" if len(self._api_key) > 4 else "***"
        return f"<PolzaProvider base_url={self._base_url} key=…{suffix}>"

    async def aclose(self) -> None:
        """Close the underlying :class:`httpx.AsyncClient`.

        Called by the worker shutdown hook in Sprint 3. Tests reach
        for it via the factory fixture so test workers don't leak
        sockets.
        """

        await self._client.aclose()

    async def complete(
        self,
        prompt: str,
        model: str,
        *,
        tools: list[Tool] | None = None,
        max_tokens: int = 2000,
    ) -> LLMResult:
        del prompt, model, tools, max_tokens
        raise NotImplementedError("Polza wire-up — Sprint 3. Set LLM_PROVIDER=mock for now.")

    async def embed(self, text: str, model: str) -> EmbeddingResult:
        del text, model
        raise NotImplementedError("Polza wire-up — Sprint 3. Set LLM_PROVIDER=mock for now.")


def build_default_provider() -> LLMProvider:
    """Construct the provider configured for the current environment.

    Resolution order:

    1. ``settings.llm_provider == "polza"`` and ``polza_api_key`` set
       → :class:`PolzaProvider`. (Sprint 3 actually exercises the
       wire-up; PR #17 only validates the env contract.)
    2. ``settings.llm_provider == "polza"`` but no API key set →
       hard fail at startup. We deliberately don't fall back to the
       mock — silently degrading to fixtures in production would
       be the worst kind of bug.
    3. Everything else → :class:`MockLLMProvider` with the default
       dimensionality from :mod:`app.models.channel_post_embedding`.
    """

    provider = settings.llm_provider.lower()
    if provider == "polza":
        if not settings.polza_api_key:
            # Loud, typed failure — a deployment with
            # ``LLM_PROVIDER=polza`` but no key is misconfigured.
            raise RuntimeError(
                "LLM_PROVIDER=polza but POLZA_API_KEY is empty. "
                "Set the API key or switch to LLM_PROVIDER=mock for tests.",
            )
        return PolzaProvider(
            api_key=settings.polza_api_key,
            base_url=settings.polza_base_url,
        )

    # Lazy import — keeps the import graph one-way (mock depends on
    # the model layer, polza shouldn't pull the mock in
    # transitively).
    from app.adapters.llm.mock import MockLLMProvider as _MockLLMProvider

    mock_cls: type[MockLLMProvider] = _MockLLMProvider
    return mock_cls(dim=settings.embedding_dim)


__all__ = [
    "PolzaProvider",
    "build_default_provider",
]
