"""Channel-post embedding persistence (PR #17).

docs/04-architecture.md §19.6 + docs/plans/phase1-sprint2-plan.md
PR #17: the service that mediates between
:class:`app.adapters.llm.LLMProvider` (vector synthesis) and the
:class:`app.models.channel_post_embedding.ChannelPostEmbedding`
table (persistence).

Public surface
--------------
* :func:`EmbeddingsService.embed_channel_post` — resolve the post,
  derive its workspace via :class:`WorkspaceChannel`, ask the
  provider for an embedding, upsert the resulting row. Idempotent
  on ``(channel_post_id, model)``.

Why a class and not a free function? The constructor captures the
``provider`` + ``model`` so the Celery task can wire them once and
pass the bound service into multiple invocations without re-reading
:mod:`app.core.config`. The session is passed per-call so the
task / route handler owns the transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.base import LLMProvider, LLMProviderError
from app.models.channel import ChannelPost, WorkspaceChannel
from app.models.channel_post_embedding import EMBEDDING_DIM, ChannelPostEmbedding

logger = structlog.get_logger(__name__)


# Allowed reasons a post is skipped without embedding. Surface as a
# stable slug so the Celery task can write a consistent audit row
# and the dashboard can render a contextual hint.
SKIP_NO_TEXT = "no_text"
SKIP_NO_BINDING = "no_binding"
SKIP_UNKNOWN_POST = "unknown_post"


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmbeddingPersistResult:
    """Outcome of one :meth:`EmbeddingsService.embed_channel_post` call.

    ``inserted`` and ``updated`` are mutually exclusive (a row was
    either freshly created or the existing row was overwritten with
    a new vector). ``skipped`` is set when the post couldn't be
    embedded — either the post doesn't exist yet, no workspace has
    bound the channel, or the post has no text to embed.
    """

    inserted: bool = False
    updated: bool = False
    skipped: str | None = None
    channel_post_id: uuid.UUID | None = None
    model: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingsService:
    """Mediates between an :class:`LLMProvider` and the embeddings table.

    Constructed once per Celery task invocation; the session is
    injected per-call so the caller (task / route handler) owns the
    transaction boundary.
    """

    provider: LLMProvider
    model: str
    dim: int = EMBEDDING_DIM

    async def embed_channel_post(
        self,
        session: AsyncSession,
        channel_post_id: uuid.UUID,
    ) -> EmbeddingPersistResult:
        """Embed one channel post and upsert the resulting vector.

        Flow:

        1. Resolve the :class:`ChannelPost`. Unknown id →
           skipped="unknown_post".
        2. Compose the embedding text: ``post.text`` (an optional
           caption is already concatenated into ``text`` by the
           ingest parser). Empty text → ``skipped="no_text"`` —
           pure-media posts are a Sprint 3 problem (caption AI).
        3. Resolve a :class:`WorkspaceChannel` binding for the post's
           channel. Multiple workspaces may have bound the same
           channel (Global Channel Registry); we just need one to
           anchor the row's ``workspace_id`` denorm. No binding →
           ``skipped="no_binding"`` (somebody detached the channel
           between ingest and embedding).
        4. Ask the provider for a vector. Validate dim. Permanent
           errors (:class:`LLMProviderError` / :class:`ValueError`)
           propagate — the Celery task records the failure. Transient
           errors propagate too (:class:`LLMTimeoutError`) so the
           task can retry.
        5. UPSERT into ``channel_post_embeddings``: on
           ``(channel_post_id, model)`` hit, update the existing row;
           else insert a fresh one. The caller commits.
        """

        post = await session.get(ChannelPost, channel_post_id)
        if post is None:
            logger.info(
                "embeddings.unknown_post",
                channel_post_id=str(channel_post_id),
            )
            return EmbeddingPersistResult(
                skipped=SKIP_UNKNOWN_POST,
                channel_post_id=channel_post_id,
                model=self.model,
            )

        text = (post.text or "").strip()
        if not text:
            logger.info(
                "embeddings.no_text",
                channel_post_id=str(channel_post_id),
            )
            return EmbeddingPersistResult(
                skipped=SKIP_NO_TEXT,
                channel_post_id=channel_post_id,
                model=self.model,
            )

        binding = await _first_active_binding(session, channel_id=post.channel_id)
        if binding is None:
            logger.info(
                "embeddings.no_binding",
                channel_post_id=str(channel_post_id),
                channel_id=str(post.channel_id),
            )
            return EmbeddingPersistResult(
                skipped=SKIP_NO_BINDING,
                channel_post_id=channel_post_id,
                model=self.model,
            )

        # PR #20 contract: ``embed`` is batched; single-text callers
        # wrap in ``[text]`` and read ``result[0]``.
        vectors = await self.provider.embed([text], self.model)
        if not vectors:
            raise LLMProviderError(
                "Provider returned empty embedding batch for one input",
            )
        vector = vectors[0]
        if len(vector) != self.dim:
            # Dim mismatch is a configuration bug — the provider
            # returned an embedding the persistence layer can't
            # store. Surface it as ``LLMProviderError`` so the
            # Celery task's retry budget isn't burned on something
            # only a re-deploy can fix.
            raise LLMProviderError(
                f"Embedding dim {len(vector)} does not match "
                f"configured dim {self.dim} for model {self.model!r}",
            )

        return await _upsert_embedding(
            session,
            channel_post_id=channel_post_id,
            channel_id=post.channel_id,
            workspace_id=binding.workspace_id,
            model=self.model,
            vector=vector,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _first_active_binding(
    session: AsyncSession,
    *,
    channel_id: uuid.UUID,
) -> WorkspaceChannel | None:
    """Return any active :class:`WorkspaceChannel` for ``channel_id``.

    The channel may be bound to multiple workspaces — for the
    embedding row we just need *one* to anchor the
    ``workspace_id`` denorm. Sprint 3 introduces per-binding
    embedding rows; until then a single row per ``(post, model)``
    is the contract. We pick the oldest active binding so the
    embedding lives in the workspace that connected the channel
    first — that's the stable choice across re-runs and matches the
    Global Channel Registry semantics (D20 in docs/03).
    """

    res = await session.execute(
        select(WorkspaceChannel)
        .where(
            WorkspaceChannel.channel_id == channel_id,
            WorkspaceChannel.disconnected_at.is_(None),
        )
        .order_by(WorkspaceChannel.connected_at.asc())
        .limit(1),
    )
    return res.scalar_one_or_none()


async def _upsert_embedding(
    session: AsyncSession,
    *,
    channel_post_id: uuid.UUID,
    channel_id: uuid.UUID,
    workspace_id: uuid.UUID,
    model: str,
    vector: list[float],
) -> EmbeddingPersistResult:
    """Insert or update the embedding row for ``(channel_post_id, model)``.

    The optimistic-then-fallback pattern (try insert, catch
    integrity error, then update) is the same one the channel
    ingest service uses for deduping posts — it survives the race
    between two workers picking up the same Celery message via the
    ``acks_late`` semantics.
    """

    existing = await _find_existing(
        session,
        channel_post_id=channel_post_id,
        model=model,
    )
    if existing is not None:
        existing.embedding = vector
        existing.workspace_id = workspace_id
        existing.channel_id = channel_id
        await session.flush()
        return EmbeddingPersistResult(
            updated=True,
            channel_post_id=channel_post_id,
            model=model,
        )

    row = ChannelPostEmbedding(
        channel_post_id=channel_post_id,
        channel_id=channel_id,
        workspace_id=workspace_id,
        model=model,
        embedding=vector,
    )
    session.add(row)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        # Raced — another worker inserted the row between our SELECT
        # and the INSERT. Fall through to the update path.
        await session.rollback()
        existing = await _find_existing(
            session,
            channel_post_id=channel_post_id,
            model=model,
        )
        if existing is None:  # pragma: no cover - defensive
            # Constraint hit but row not visible: something exotic
            # at the DB layer; surface to the task so it retries.
            raise
        existing.embedding = vector
        existing.workspace_id = workspace_id
        existing.channel_id = channel_id
        await session.flush()
        return EmbeddingPersistResult(
            updated=True,
            channel_post_id=channel_post_id,
            model=model,
        )

    return EmbeddingPersistResult(
        inserted=True,
        channel_post_id=channel_post_id,
        model=model,
    )


async def _find_existing(
    session: AsyncSession,
    *,
    channel_post_id: uuid.UUID,
    model: str,
) -> ChannelPostEmbedding | None:
    res = await session.execute(
        select(ChannelPostEmbedding).where(
            ChannelPostEmbedding.channel_post_id == channel_post_id,
            ChannelPostEmbedding.model == model,
        ),
    )
    return res.scalar_one_or_none()


__all__ = [
    "SKIP_NO_BINDING",
    "SKIP_NO_TEXT",
    "SKIP_UNKNOWN_POST",
    "EmbeddingPersistResult",
    "EmbeddingsService",
]
