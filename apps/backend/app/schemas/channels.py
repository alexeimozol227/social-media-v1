"""Channel API request / response schemas (PR #14).

docs/plans/phase1-sprint2-plan.md §"Бэкенд — API эндпоинты":

* :class:`ConnectChannelRequest` — body of
  ``POST /v1/brands/{brand_id}/channels``.
* :class:`ChannelView` — read-side projection used by both the
  list and detail responses.
* :class:`ChannelListResponse` — paginated list shape.
* :class:`BrandSummary` — used by ``GET /v1/users/me/brands`` so
  the brand-switcher UI can render the dropdown without leaking
  internal fields.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str | int,
    Field(
        description=(
            "Channel identifier. Accepted shapes:"
            " ``@username`` (with or without the leading ``@``),"
            " a numeric ``chat.id`` (``-100…`` for supergroups / channels),"
            " or a public ``https://t.me/<username>`` URL."
            " Private invite links (``t.me/+<hash>`` / ``t.me/joinchat/<hash>``)"
            " can't be resolved by the Bot API and are rejected with 422."
        ),
    ),
]


# Matches ``-100123…`` / ``-123…`` / ``123…`` so we coerce them to ``int``
# *before* passing to the Bot API. ``int`` and ``@username`` dispatch on
# different Bot API paths.
_NUMERIC_CHAT_ID_RE = re.compile(r"^-?\d+$")

# Public Telegram handle character set per Bot API: 5-32 chars, letters,
# digits, underscores. We keep this loose (no length check, no
# starts-with-letter rule) because rejecting a typo here would be more
# annoying than letting Telegram return ``chat not found``.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _normalize_identifier(raw: str | int) -> str | int:
    """Coerce the various user-supplied shapes into one the Bot API likes.

    The Bot API's ``chat_id`` parameter accepts either:

    * a numeric ``chat.id`` (``int``, negative for supergroups / channels), or
    * a ``@channelusername`` literal for **public** channels / groups.

    Users tend to paste:

    * ``@channelusername`` — fine, we strip the ``@``.
    * ``https://t.me/<username>`` / ``t.me/<username>`` — looks like a
      public handle URL; we strip the host prefix so the bare username
      is passed downstream. (Note: Telegram serves a public web page
      for the **title** of a private group too, but ``getChat`` can't
      resolve it — those users need the numeric chat id.)
    * ``-1003983437401`` — numeric chat id; we coerce it to ``int``.
    * Invite links ``t.me/+<hash>`` or ``t.me/joinchat/<hash>`` — the
      bot can't ``getChat`` these (only users can join). Reject up front
      with a clear 422 instead of bouncing off Bot API as 404.
    * ``https://t.me/c/<id>/<msg>`` — the desktop "copy link to
      message" shape for private groups. ``<id>`` is the supergroup
      id without the ``-100`` prefix; we reattach it so the user
      doesn't have to do the math by hand.

    Whitespace, surrounding ``<…>`` from chat clients, and the leading
    ``@`` are all stripped. Anything that survives this funnel but
    looks neither numeric nor like a Telegram username is passed
    through — Telegram's own 400 → ``CHANNEL_NOT_FOUND_ON_PLATFORM``
    is the right error in that case.
    """

    if isinstance(raw, int):
        return raw

    s = raw.strip()
    # Some chat clients wrap pasted links in ``<…>`` (Markdown).
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()

    # Strip scheme + telegram hostnames so the remainder is the path.
    for scheme in ("https://", "http://"):
        if s.lower().startswith(scheme):
            s = s[len(scheme) :]
            break
    for host in ("www.t.me/", "www.telegram.me/", "t.me/", "telegram.me/"):
        if s.lower().startswith(host):
            s = s[len(host) :]
            break

    # Drop a trailing path / query (``t.me/foo/123?bar=baz`` → ``foo``).
    # The ``c/<id>/<msg>`` private-link shape needs special handling
    # *before* we slice off the second path segment.
    if s.lower().startswith("c/"):
        rest = s[2:]
        head = rest.split("/", 1)[0]
        if _NUMERIC_CHAT_ID_RE.fullmatch(head) is None:
            msg = (
                "Unrecognized t.me/c/<id> link. The first path segment must be the"
                " numeric supergroup id; for example t.me/c/1234567890/42."
            )
            raise ValueError(msg)
        # The web client strips the ``-100`` prefix from the chat id in
        # the URL; reattach it.
        return int(f"-100{head}")

    if "?" in s:
        s = s.split("?", 1)[0]
    if "/" in s:
        s = s.split("/", 1)[0]

    # Strip an optional leading ``@``.
    if s.startswith("@"):
        s = s[1:]

    if not s:
        msg = "identifier must be a non-empty @username, chat id or t.me URL"
        raise ValueError(msg)

    # Invite links — bots can't resolve these via ``getChat``.
    if s.startswith("+") or s.lower().startswith("joinchat"):
        msg = (
            "Private invite links (t.me/+… or t.me/joinchat/…) can't be resolved by"
            " the Bot API. Use the @username for public channels or the numeric"
            " chat id (-100…) for private groups instead."
        )
        raise ValueError(msg)

    # Numeric chat id → ``int`` so the Bot API picks the numeric path.
    if _NUMERIC_CHAT_ID_RE.fullmatch(s):
        return int(s)

    # Anything else is treated as a username candidate. Don't enforce
    # the strict regex — Telegram returns a clean 400 for malformed
    # handles, and a wrong regex here would be more annoying than the
    # downstream error.
    if _USERNAME_RE.fullmatch(s) is None:
        # Still reject obvious shapes that can't possibly resolve so
        # the user gets actionable feedback instead of "not found".
        msg = (
            "identifier must be a @username (letters / digits / underscores),"
            " a numeric chat id, or a https://t.me/<username> URL"
        )
        raise ValueError(msg)
    return s


class ConnectChannelRequest(BaseModel):
    """Body of ``POST /v1/brands/{brand_id}/channels``.

    The ``platform`` field is currently a single-value enum
    (``telegram``) but kept open so PR #18 can add a second value
    without breaking the API. ``identifier`` is loose-typed because
    the Bot API accepts both ``@username`` (string) and numeric
    ``chat_id`` (int) — the wrapper handles the dispatch.
    """

    model_config = ConfigDict(extra="forbid")

    platform: Literal["telegram"] = Field(
        default="telegram",
        description="Social platform identifier. ``telegram`` on MVP.",
    )
    identifier: Identifier

    @model_validator(mode="after")
    def _strip_identifier(self) -> ConnectChannelRequest:
        # Normalize the various shapes (URLs, ``@handle``, numeric id)
        # into one the Bot API likes. Empty strings / invite links are
        # rejected up front so the user gets a 422 with a useful
        # ``ValidationError`` body instead of a Bot API 404.
        object.__setattr__(self, "identifier", _normalize_identifier(self.identifier))
        return self


class ChannelView(BaseModel):
    """Read-side projection of a connected channel.

    Combines :class:`Channel` (registry) + :class:`WorkspaceChannel`
    (binding) into one flat shape so the SPA doesn't have to do a
    second look-up to find the public ``@handle``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="``workspace_channels.id`` — brand-scoped binding id.")
    channel_id: uuid.UUID = Field(description="``channels.id`` — Global Channel Registry id.")
    platform: str = Field(description="Channel platform.")
    external_id: int = Field(description="Telegram ``chat.id``.")
    username: Annotated[str | None, StringConstraints(max_length=64)] = Field(
        default=None,
        description="``@handle`` without the leading ``@``; None for private channels.",
    )
    title: Annotated[str | None, StringConstraints(max_length=255)] = Field(
        default=None,
        description="Display name.",
    )
    role: str = Field(description="``owned`` / ``competitor``.")
    bot_admin_rights: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of the bot's admin rights at connect / verify time.",
    )
    connected_at: datetime
    disconnected_at: datetime | None = None


class ChannelListResponse(BaseModel):
    """Paginated channel list returned by ``GET /v1/brands/{id}/channels``."""

    items: list[ChannelView]
    total: int = Field(description="Total channels matching the filter (across pages).")


class BrandSummary(BaseModel):
    """Brand-switcher payload (``GET /v1/users/me/brands``).

    Trims :class:`app.models.brand.Brand` down to the fields the SPA
    needs to render the dropdown.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: Annotated[str, StringConstraints(max_length=255)]
    is_default: bool
    content_language: Annotated[str, StringConstraints(max_length=16)]
    timezone: Annotated[str, StringConstraints(max_length=64)]


__all__ = [
    "BrandSummary",
    "ChannelListResponse",
    "ChannelView",
    "ConnectChannelRequest",
]
