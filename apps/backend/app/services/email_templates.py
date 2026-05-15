"""Plain-text email templates (RU + EN).

Adapted from the reference project. The MVP is RU-first
(``docs/04-architecture.md §22 D63``); EN is included so a future
locale switch in user settings has somewhere to fall back to.

The "ты"-form, sentence case, no exclamation points are intentional.
Templates are short enough to render fine in any mail client without
a templating engine — we deliberately don't ship HTML email here.
"""

from __future__ import annotations

from typing import Literal

Lang = Literal["ru", "en"]


_SIGNUP_RU = (
    "Подтверди свой email в social-media-v1.\n"
    "\n"
    "Введи этот код в окне подтверждения — он живёт {ttl_minutes} минут:\n"
    "\n"
    "    {code}\n"
    "\n"
    "Если ты не регистрировался(ась), просто проигнорируй письмо.\n"
)

_SIGNUP_EN = (
    "Confirm your social-media-v1 email.\n"
    "\n"
    "Enter this code in the verification dialog — it expires in "
    "{ttl_minutes} minutes:\n"
    "\n"
    "    {code}\n"
    "\n"
    "If you didn't sign up, you can ignore this message.\n"
)


def signup_verification(
    *,
    code: str,
    ttl_minutes: int,
    lang: Lang = "ru",
) -> tuple[str, str]:
    """Returns ``(subject, body)`` for the sign-up verification email."""

    if lang == "en":
        return (
            "Confirm your social-media-v1 email",
            _SIGNUP_EN.format(code=code, ttl_minutes=ttl_minutes),
        )
    return (
        "Подтверди email в social-media-v1",
        _SIGNUP_RU.format(code=code, ttl_minutes=ttl_minutes),
    )


_CHANGE_RU = (
    "Подтверди новый email в social-media-v1.\n"
    "\n"
    "Чтобы заменить email на текущий аккаунт, введи код в форме смены — "
    "он живёт {ttl_minutes} минут:\n"
    "\n"
    "    {code}\n"
    "\n"
    "Если ты не запрашивал смену email, просто проигнорируй письмо.\n"
)

_CHANGE_EN = (
    "Confirm your new social-media-v1 email.\n"
    "\n"
    "Enter this code in the change-email form to swap your account "
    "email — it expires in {ttl_minutes} minutes:\n"
    "\n"
    "    {code}\n"
    "\n"
    "If you didn't request an email change, you can ignore this "
    "message.\n"
)


def change_verification(
    *,
    code: str,
    ttl_minutes: int,
    lang: Lang = "ru",
) -> tuple[str, str]:
    """Returns ``(subject, body)`` for the email-change verification.

    Routes shipping in PR #3 only exercise ``signup``; the ``change``
    flow is wired in a follow-up. Defined here so PR-F2 doesn't churn
    this module.
    """

    if lang == "en":
        return (
            "Confirm your new social-media-v1 email",
            _CHANGE_EN.format(code=code, ttl_minutes=ttl_minutes),
        )
    return (
        "Подтверди новый email в social-media-v1",
        _CHANGE_RU.format(code=code, ttl_minutes=ttl_minutes),
    )


_RESET_RU = (
    "Восстановление пароля social-media-v1.\n"
    "\n"
    "Кто-то запросил сброс пароля для этого аккаунта. Если это был ты,\n"
    "перейди по ссылке — она живёт {ttl_minutes} минут:\n"
    "\n"
    "    {reset_url}\n"
    "\n"
    "Если ты не запрашивал(а) сброс — просто проигнорируй письмо. Пароль\n"
    "не изменится сам собой.\n"
)

_RESET_EN = (
    "Reset your social-media-v1 password.\n"
    "\n"
    "Someone asked to reset the password on this account. If it was you,\n"
    "open the link — it expires in {ttl_minutes} minutes:\n"
    "\n"
    "    {reset_url}\n"
    "\n"
    "If you didn't request a reset, you can ignore this message. Your\n"
    "password won't change unless you open the link.\n"
)


def password_reset(
    *,
    reset_url: str,
    ttl_minutes: int,
    lang: Lang = "ru",
) -> tuple[str, str]:
    """Returns ``(subject, body)`` for the password-reset email."""

    if lang == "en":
        return (
            "Reset your social-media-v1 password",
            _RESET_EN.format(reset_url=reset_url, ttl_minutes=ttl_minutes),
        )
    return (
        "Восстановление пароля social-media-v1",
        _RESET_RU.format(reset_url=reset_url, ttl_minutes=ttl_minutes),
    )


_RESET_DONE_RU = (
    "Пароль на твоём аккаунте social-media-v1 изменён.\n"
    "\n"
    "Если это был ты — ничего делать не нужно. Если нет, напиши в поддержку,\n"
    "и мы заблокируем сессии и сбросим пароль повторно.\n"
)

_RESET_DONE_EN = (
    "Your social-media-v1 password was reset.\n"
    "\n"
    "If this was you, no action needed. If not, contact support — we'll\n"
    "lock the account and walk you through a fresh reset.\n"
)


def password_reset_done(*, lang: Lang = "ru") -> tuple[str, str]:
    """Courtesy email sent **after** a successful password reset.

    Best-effort — the route handler does not roll back the password
    change if the email fails to send.
    """

    if lang == "en":
        return ("Your social-media-v1 password was reset", _RESET_DONE_EN)
    return ("Пароль на social-media-v1 изменён", _RESET_DONE_RU)


_MFA_ENROLLED_RU = (
    "Двухфакторная аутентификация (2FA) включена.\n"
    "\n"
    "Теперь при входе на social-media-v1 нужно вводить код из "
    "приложения-аутентификатора в дополнение к паролю.\n"
    "\n"
    "Если это сделал не ты, срочно смени пароль на social-media-v1 "
    "и отключи 2FA в настройках безопасности.\n"
)

_MFA_ENROLLED_EN = (
    "Two-factor authentication (2FA) was enabled.\n"
    "\n"
    "From now on, signing in to social-media-v1 requires a code from "
    "your authenticator app in addition to your password.\n"
    "\n"
    "If this wasn't you, change your social-media-v1 password and "
    "disable 2FA in Security settings immediately.\n"
)


def mfa_enrolled(*, lang: Lang = "ru") -> tuple[str, str]:
    """Courtesy email confirming 2FA was enabled on the account."""

    if lang == "en":
        return ("Two-factor authentication enabled", _MFA_ENROLLED_EN)
    return ("Двухфакторная аутентификация включена", _MFA_ENROLLED_RU)


_MFA_DISABLED_RU = (
    "Двухфакторная аутентификация (2FA) отключена.\n"
    "\n"
    "Если это сделал не ты, срочно смени пароль на social-media-v1.\n"
)

_MFA_DISABLED_EN = (
    "Two-factor authentication (2FA) was disabled.\n"
    "\n"
    "If this wasn't you, change your social-media-v1 password "
    "immediately.\n"
)


def mfa_disabled(*, lang: Lang = "ru") -> tuple[str, str]:
    """Courtesy email confirming 2FA was disabled."""

    if lang == "en":
        return ("Two-factor authentication disabled", _MFA_DISABLED_EN)
    return ("Двухфакторная аутентификация отключена", _MFA_DISABLED_RU)


__all__ = [
    "Lang",
    "change_verification",
    "mfa_disabled",
    "mfa_enrolled",
    "password_reset",
    "password_reset_done",
    "signup_verification",
]
