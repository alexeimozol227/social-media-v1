"""Telegram live-ingest integration (PR #16).

The webhook entry point lives in :mod:`app.api.routes.integrations`;
this package hosts the dispatcher + message parsers it delegates
to. Keeping the aiogram-specific glue here (rather than in the
``adapters`` package) preserves the rule that ``adapters/social``
only exposes a transport-shaped contract \u2014 the live-ingest plumbing
needs the dispatcher / router types that don't make sense on the
generic adapter Protocol.
"""

from app.integrations.telegram.dispatcher import build_dispatcher
from app.integrations.telegram.parsers import message_to_snapshot

__all__ = [
    "build_dispatcher",
    "message_to_snapshot",
]
