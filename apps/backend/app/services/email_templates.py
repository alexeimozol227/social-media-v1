"""Thin wrappers around the localised email catalog.

The actual subject / plain-text / HTML copy lives in
``app/locales/{ru,en}/email/*.{subject,html,txt}.jinja`` — see
``app/core/translations.py`` for the loader. This module preserves
the existing call signatures (``signup_verification``,
``change_verification``, …) so the service layer doesn't need to
know there's a Jinja2 catalog behind them.

Each function returns a :class:`RenderedEmail` triple
(``subject, text_body, html_body``) so the SMTP / HTTP transport can
ship the message as ``multipart/alternative``. Mail clients that
can't render HTML fall back to the plain-text view — see
``docs/05-tech-stack.md §3.6``.

Adding a new email:

1. Create the three Jinja2 files
   (``email/<key>.subject.jinja``, ``…/<key>.txt.jinja``,
   ``…/<key>.html.jinja``) under **each** supported locale in
   ``app/locales/``.
2. Add a wrapper function here that calls :func:`_render` with the
   matching key and your template variables.

That's it — no Python copy edits, no second deploy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from app.core.translations import render_template

# Re-export for backwards compatibility — services still type their
# ``lang`` parameter as ``email_templates.Lang``.
Lang = Literal["ru", "en"]

# Branded product name interpolated into every template. Centralised
# here so a rename / white-label only touches one source line. Kept
# constant on purpose — making this a runtime setting would mean the
# catalog values need to be valid Jinja2 in every translation, which
# adds churn for marketing edits.
PRODUCT_NAME: Final[str] = "social-media-v1"


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """Concrete render of a transactional email.

    The ``html`` body is always set — every template ships an HTML
    variant. Transports that don't support multipart should send the
    plain-text ``body`` and discard ``html``.
    """

    subject: str
    body: str
    html: str


def _render(
    key: str,
    *,
    lang: Lang,
    **variables: object,
) -> RenderedEmail:
    """Render the (subject, text, html) triple for ``key`` in ``lang``.

    ``key`` is the filename stem under ``locales/{lang}/email/``
    (e.g. ``"signup"`` → ``signup.subject.jinja`` +
    ``signup.txt.jinja`` + ``signup.html.jinja``).
    """

    ctx: dict[str, object] = {
        "product_name": PRODUCT_NAME,
        "year": datetime.now(tz=UTC).year,
        **variables,
    }
    subject = render_template(f"email/{key}.subject.jinja", lang, **ctx).strip()
    text = render_template(f"email/{key}.txt.jinja", lang, **ctx)
    html = render_template(f"email/{key}.html.jinja", lang, **ctx)
    return RenderedEmail(subject=subject, body=text, html=html)


def signup_verification(
    *,
    code: str,
    ttl_minutes: int,
    lang: Lang = "ru",
) -> RenderedEmail:
    """Sign-up email-verification code (six-digit OTP)."""

    return _render("signup", lang=lang, code=code, ttl_minutes=ttl_minutes)


def change_verification(
    *,
    code: str,
    ttl_minutes: int,
    lang: Lang = "ru",
) -> RenderedEmail:
    """Email-change verification code (old email stays active until confirmed)."""

    return _render("change_verification", lang=lang, code=code, ttl_minutes=ttl_minutes)


def password_reset(
    *,
    reset_url: str,
    ttl_minutes: int,
    lang: Lang = "ru",
) -> RenderedEmail:
    """One-time password-reset link."""

    return _render("password_reset", lang=lang, reset_url=reset_url, ttl_minutes=ttl_minutes)


def password_reset_done(*, lang: Lang = "ru") -> RenderedEmail:
    """Courtesy email sent **after** a successful password reset.

    Best-effort — the route handler does not roll back the password
    change if the email fails to send.
    """

    return _render("password_reset_done", lang=lang)


def mfa_enrolled(*, lang: Lang = "ru") -> RenderedEmail:
    """Courtesy email confirming 2FA was enabled on the account."""

    return _render("mfa_enrolled", lang=lang)


def mfa_disabled(*, lang: Lang = "ru") -> RenderedEmail:
    """Courtesy email confirming 2FA was disabled."""

    return _render("mfa_disabled", lang=lang)


__all__ = [
    "PRODUCT_NAME",
    "Lang",
    "RenderedEmail",
    "change_verification",
    "mfa_disabled",
    "mfa_enrolled",
    "password_reset",
    "password_reset_done",
    "signup_verification",
]
