"""Locale / i18n helpers.

The backend never reads ``lang`` from a request body — clients
signal their language via the standard ``Accept-Language`` header
instead. The frontend (Next.js + next-intl) overrides the browser
default with the user's chosen locale, so a user whose browser is
configured for Spanish but who clicked the "English" toggle in the
UI gets English emails — see ``apps/web/src/lib/api.ts``.

Only two locales are supported for MVP: Russian (default) and
English. Everything else falls back to Russian per
``docs/04-architecture.md §13`` (Russian-first product).
"""

from __future__ import annotations

import re
from typing import Final, Literal

from fastapi import Request

Locale = Literal["ru", "en"]

DEFAULT_LOCALE: Final[Locale] = "ru"
SUPPORTED_LOCALES: Final[frozenset[Locale]] = frozenset({"ru", "en"})

# Parses one ``Accept-Language`` segment: a language tag plus an
# optional ``;q=…`` weight. Per RFC 9110 §12.5.4 the weight is a
# decimal between 0.0 and 1.0; absent ⇒ 1.0.
_SEGMENT_RE = re.compile(
    r"""
    ^
    (?P<tag>[A-Za-z*]{1,8}(?:-[A-Za-z0-9]{1,8})*)   # ``ru``, ``en-US``, ``*``
    \s*
    (?: ;\s* q\s*=\s* (?P<q>0(?:\.\d{0,3})? | 1(?:\.0{0,3})?) )?
    \s*
    $
    """,
    re.VERBOSE,
)


def _normalise(tag: str) -> str:
    """Reduce ``en-US`` → ``en`` (we don't have regional emails).

    ``*`` (the wildcard) is normalised to the default locale so any
    catch-all clause picks our preferred language.
    """

    primary = tag.split("-", 1)[0].lower()
    if primary == "*":
        return DEFAULT_LOCALE
    return primary


def parse_accept_language(header: str | None) -> Locale:
    """Return the best supported locale for the given header value.

    Implements RFC 9110 §12.5.4 quality-value ordering: segments are
    sorted by ``q`` descending, then by header order. We return the
    first segment whose primary tag matches one of
    :data:`SUPPORTED_LOCALES`. Anything else (missing header, only
    unsupported tags, malformed segments) falls back to
    :data:`DEFAULT_LOCALE`.
    """

    if not header:
        return DEFAULT_LOCALE

    scored: list[tuple[float, int, Locale]] = []
    for index, raw in enumerate(header.split(",")):
        match = _SEGMENT_RE.match(raw.strip())
        if match is None:
            continue
        tag = _normalise(match.group("tag"))
        if tag not in SUPPORTED_LOCALES:
            continue
        q_raw = match.group("q")
        q = 1.0 if q_raw is None else float(q_raw)
        if q <= 0.0:
            # Explicit ``q=0`` means "do NOT send me this language".
            continue
        # ``index`` preserves header order for the tie-break: higher
        # q wins, then earlier in the header wins.
        scored.append((q, -index, tag))

    if not scored:
        return DEFAULT_LOCALE
    scored.sort(reverse=True)
    return scored[0][2]


def get_locale(request: Request) -> Locale:
    """FastAPI dependency: resolve the locale for this request.

    Mounted with ``Depends(get_locale)`` in any route that renders
    user-visible copy (emails, in particular). The frontend always
    sets ``Accept-Language`` to the user's chosen UI locale so the
    in-product language toggle takes precedence over the browser
    default — see ``apps/web/src/lib/api.ts``.
    """

    return parse_accept_language(request.headers.get("accept-language"))


__all__ = [
    "DEFAULT_LOCALE",
    "SUPPORTED_LOCALES",
    "Locale",
    "get_locale",
    "parse_accept_language",
]
