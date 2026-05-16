"""Unit tests for :func:`app.schemas.channels._normalize_identifier`.

The Telegram Bot API only accepts numeric ``chat_id`` or ``@channelusername``
for public chats. Real users paste a much wider zoo of shapes — full
``https://t.me/<name>`` URLs, supergroup web links with the ``-100``
prefix stripped, even invite links the bot can never resolve. This
test suite pins the normalization contract so the route handler always
hands the Bot API a string it likes.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.channels import ConnectChannelRequest, _normalize_identifier


class TestNormalizeIdentifierUsernames:
    """Bare ``@username`` / username / URL handle shapes."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("test_channel", "test_channel"),
            ("@test_channel", "test_channel"),
            ("  @test_channel  ", "test_channel"),
            ("<@test_channel>", "test_channel"),
            ("https://t.me/test_channel", "test_channel"),
            ("http://t.me/test_channel", "test_channel"),
            ("HTTPS://T.ME/test_channel", "test_channel"),
            ("t.me/test_channel", "test_channel"),
            ("telegram.me/test_channel", "test_channel"),
            ("www.t.me/test_channel", "test_channel"),
            ("https://t.me/test_channel/", "test_channel"),
            ("https://t.me/test_channel?utm=foo", "test_channel"),
            ("https://t.me/test_channel/12345", "test_channel"),
        ],
    )
    def test_strips_url_and_at_prefix(self, raw: str, expected: str) -> None:
        assert _normalize_identifier(raw) == expected


class TestNormalizeIdentifierNumericChatId:
    """Numeric chat id shapes are coerced to ``int`` so the Bot API
    wrapper dispatches on the numeric path."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("-1003983437401", -1003983437401),
            ("-1001234567890", -1001234567890),
            ("123456789", 123456789),
            ("  -1003983437401  ", -1003983437401),
            (-1003983437401, -1003983437401),
            (123, 123),
        ],
    )
    def test_numeric_chat_id_coerced_to_int(self, raw: str | int, expected: int) -> None:
        assert _normalize_identifier(raw) == expected
        assert isinstance(_normalize_identifier(raw), int)


class TestNormalizeIdentifierPrivateWebLink:
    """``t.me/c/<id>/<msg>`` is the "copy link to message" shape for
    private supergroups. Telegram strips the ``-100`` prefix from the
    chat id in the URL; we put it back."""

    def test_c_link_reattaches_minus_100_prefix(self) -> None:
        # https://t.me/c/3983437401/42 → -1003983437401
        assert _normalize_identifier("https://t.me/c/3983437401/42") == -1003983437401

    def test_c_link_without_message_id(self) -> None:
        assert _normalize_identifier("t.me/c/3983437401") == -1003983437401

    def test_c_link_with_query_string(self) -> None:
        assert _normalize_identifier("https://t.me/c/3983437401/42?single") == -1003983437401

    def test_c_link_non_numeric_id_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"t\.me/c/<id>"):
            _normalize_identifier("https://t.me/c/not_a_number/42")


class TestNormalizeIdentifierRejected:
    """Shapes the Bot API can never resolve — fail fast with 422."""

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "@",
            "https://t.me/",
            "t.me/",
        ],
    )
    def test_empty_after_strip_is_rejected(self, raw: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            _normalize_identifier(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            "https://t.me/+abcDEF123",
            "t.me/+abcDEF123",
            "https://t.me/joinchat/abcDEF123",
            "t.me/joinchat/abcDEF123",
            "+abcDEF123",
        ],
    )
    def test_invite_links_are_rejected(self, raw: str) -> None:
        with pytest.raises(ValueError, match="Private invite links"):
            _normalize_identifier(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            "with space",
            "name-with-dash",
            "name.with.dot",
            "name!bang",
        ],
    )
    def test_invalid_username_chars_are_rejected(self, raw: str) -> None:
        with pytest.raises(
            ValueError,
            match=r"@username|chat id|t\.me",
        ):
            _normalize_identifier(raw)


class TestConnectChannelRequestSchema:
    """End-to-end through the Pydantic model so we know the validator
    is wired in and the response error shape (422 with ``ValidationError``)
    is what FastAPI surfaces to the client."""

    def test_username_with_at_sign(self) -> None:
        req = ConnectChannelRequest.model_validate({"identifier": "@my_channel"})
        assert req.identifier == "my_channel"

    def test_full_url(self) -> None:
        req = ConnectChannelRequest.model_validate({"identifier": "https://t.me/my_channel"})
        assert req.identifier == "my_channel"

    def test_numeric_chat_id_string_coerced(self) -> None:
        req = ConnectChannelRequest.model_validate({"identifier": "-1003983437401"})
        assert req.identifier == -1003983437401
        assert isinstance(req.identifier, int)

    def test_numeric_chat_id_int_passthrough(self) -> None:
        req = ConnectChannelRequest.model_validate({"identifier": -1003983437401})
        assert req.identifier == -1003983437401

    def test_private_c_link_converted(self) -> None:
        req = ConnectChannelRequest.model_validate({"identifier": "https://t.me/c/3983437401/42"})
        assert req.identifier == -1003983437401

    def test_invite_link_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ConnectChannelRequest.model_validate({"identifier": "https://t.me/+abcDEF123"})
        # Pydantic wraps the ValueError; ensure the human message
        # survived so the client gets actionable feedback.
        assert "Private invite links" in str(exc_info.value)

    def test_empty_identifier_raises(self) -> None:
        with pytest.raises(ValidationError):
            ConnectChannelRequest.model_validate({"identifier": ""})

    def test_extra_field_rejected(self) -> None:
        # ``extra="forbid"`` is part of the contract — guard against
        # accidental schema-loosening regressions.
        with pytest.raises(ValidationError):
            ConnectChannelRequest.model_validate({"identifier": "test", "secret_admin_flag": True})

    def test_unsupported_platform_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ConnectChannelRequest.model_validate({"identifier": "test", "platform": "instagram"})
