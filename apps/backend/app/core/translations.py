"""Backend i18n catalog — loader for ``app/locales/{lang}/*.json``.

The backend is bilingual (RU primary, EN fallback) per
``docs/04-architecture.md §22 D63``. **No** user-visible copy is
hardcoded in Python source: error messages, email subjects, email
bodies (text + HTML), and any other localised string live in JSON
or Jinja2 files under :data:`LOCALES_DIR`, indexed by language.

Two loaders:

* :func:`t` — flat-key translator backed by JSON. Used for short
  strings (error messages, single-line UI copy).
* :func:`render_template` — Jinja2 renderer for multi-line templates
  with variable substitution. Used for emails (subject / plain-text
  body / HTML body).

Both fall back to :data:`DEFAULT_LOCALE` (``ru``) when a key is
missing in the requested locale, then to the raw key as a last
resort so a forgotten translation surfaces in logs without a hard
failure.

Catalog layout::

    app/locales/
        ru/
            errors.json           # error_code → human message
            ui.json               # short UI strings (route docstrings …)
            email/
                signup.subject.jinja
                signup.txt.jinja
                signup.html.jinja
                password_reset.subject.jinja
                …
        en/
            errors.json
            ui.json
            email/
                …

Production reads the catalog **once** at import time and caches the
parsed JSON / compiled Jinja2 environments. Tests re-import via
:func:`_reload_catalog_for_tests` so a fixture can patch the locale
dir without bleed-through.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Final, cast

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, Locale

logger = structlog.get_logger(__name__)

# Locales directory lives at ``app/locales``. Resolving from this
# file's path keeps the catalog co-located with the source — no env
# var, no runtime-config knob, so accidental misconfig can't ship a
# build with broken translations.
LOCALES_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "locales"


def _load_json_catalog(lang: Locale, filename: str) -> dict[str, str]:
    path = LOCALES_DIR / lang / filename
    if not path.is_file():
        logger.warning("translations.catalog_missing", lang=lang, file=filename, path=str(path))
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Catalog corruption is a deploy-time concern — log loudly so
        # ops sees it, but don't crash startup; the fallback chain
        # below will surface the bad locale as English copy.
        logger.error(
            "translations.catalog_invalid_json",
            lang=lang,
            file=filename,
            error=str(exc),
        )
        return {}
    if not isinstance(raw, dict):
        logger.error(
            "translations.catalog_unexpected_shape",
            lang=lang,
            file=filename,
            type=type(raw).__name__,
        )
        return {}
    return {str(k): str(v) for k, v in raw.items()}


@cache
def _json_catalog(lang: Locale, filename: str) -> dict[str, str]:
    return _load_json_catalog(lang, filename)


def t(key: str, lang: Locale = DEFAULT_LOCALE, /, **variables: object) -> str:
    """Translate ``key`` into ``lang`` (falling back to RU then to ``key``).

    The catalog is split per filename — the *first* dotted segment of
    ``key`` selects the file (``errors.CHANNEL_NOT_FOUND`` →
    ``errors.json[CHANNEL_NOT_FOUND]``). Variable substitution uses
    Python's :meth:`str.format` semantics so callers can pass named
    placeholders matching the ``{name}`` slots in the catalog value.

    Missing keys are logged once and returned as the raw key so a
    typo on the call site is immediately visible in the response.
    """

    if "." not in key:
        raise ValueError(f"translation key must be 'namespace.identifier', got {key!r}")
    namespace, identifier = key.split(".", 1)
    filename = f"{namespace}.json"

    catalog = _json_catalog(lang, filename)
    value = catalog.get(identifier)
    if value is None and lang != DEFAULT_LOCALE:
        # Fall back to the project's primary locale before giving up.
        fallback_catalog = _json_catalog(DEFAULT_LOCALE, filename)
        value = fallback_catalog.get(identifier)
    if value is None:
        logger.warning(
            "translations.missing_key",
            key=key,
            lang=lang,
            fallback=DEFAULT_LOCALE,
        )
        return key

    if not variables:
        return value
    try:
        return value.format(**variables)
    except (KeyError, IndexError) as exc:
        # A template referencing ``{ttl_minutes}`` with no caller
        # value would otherwise raise; surface it as a log + raw
        # template so the response still goes out.
        logger.warning(
            "translations.format_failed",
            key=key,
            lang=lang,
            error=str(exc),
        )
        return value


# ---------------------------------------------------------------------------
# Jinja2 — email templates
# ---------------------------------------------------------------------------


@cache
def _jinja_env(lang: Locale) -> Environment:
    """One ``Environment`` per locale, cached for the process lifetime.

    ``StrictUndefined`` so a missing variable is a loud error in dev
    rather than a silently-empty placeholder in a production email.
    ``select_autoescape`` only auto-escapes HTML templates; plain-text
    ``.txt.jinja`` files are left raw so newlines don't sprout
    ``&#10;``.
    """

    return Environment(
        loader=FileSystemLoader(str(LOCALES_DIR / lang)),
        autoescape=select_autoescape(("html", "htm", "html.jinja")),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_template(
    template_name: str,
    lang: Locale = DEFAULT_LOCALE,
    /,
    **variables: object,
) -> str:
    """Render a Jinja2 template from the localised catalog.

    ``template_name`` is the path relative to ``app/locales/{lang}/``
    (e.g. ``email/signup.subject.jinja``). Falls back to the default
    locale on ``TemplateNotFound`` so a not-yet-translated email
    quietly ships the RU version rather than blowing up.
    """

    env = _jinja_env(lang)
    try:
        template = env.get_template(template_name)
    except Exception:
        if lang == DEFAULT_LOCALE:
            raise
        logger.warning(
            "translations.template_missing",
            template=template_name,
            lang=lang,
            fallback=DEFAULT_LOCALE,
        )
        env = _jinja_env(DEFAULT_LOCALE)
        template = env.get_template(template_name)
    return template.render(**variables)


def _reload_catalog_for_tests() -> None:
    """Clear LRU caches so tests can patch the locale dir mid-run.

    Public-private: only call this from test fixtures. Production
    code never touches the cache — the catalog is read-only after
    the first translation request.
    """

    _json_catalog.cache_clear()
    _jinja_env.cache_clear()


def supported_locales() -> tuple[Locale, ...]:
    """Tuple of supported locales — useful for tests / sanity checks."""

    # ``frozenset`` is unordered; sort by SUPPORTED_LOCALES membership
    # so the result is deterministic.
    return cast(tuple[Locale, ...], tuple(sorted(SUPPORTED_LOCALES)))


__all__ = [
    "LOCALES_DIR",
    "render_template",
    "supported_locales",
    "t",
]
