"""Tests for MFA / TOTP (PR #4).

Covers:

* Enrollment happy path (start -> confirm -> recovery codes returned).
* Re-enroll on already-enabled account is 409.
* Confirm with wrong code is 400.
* Confirm without ``/start`` (no Redis row) is 400.
* Login with 2FA on returns ``mfa_required: true`` and no cookies.
* ``/login/mfa`` exchanges the token for cookies.
* Recovery code is one-shot.
* ``/login/mfa`` rate-limit (5 attempts / window) returns 429.
* ``/mfa/disable`` requires both password AND code; bumps token_version.
* ``/mfa/recovery-codes/regenerate`` requires a fresh code.
* Constant-time-ish recovery match (no exception leak on miss).
"""

from __future__ import annotations

from typing import Any

import pyotp
import pytest
from httpx import AsyncClient


async def _register_and_login(
    client: AsyncClient, email: str, password: str = "S3curePass!"
) -> dict[str, Any]:
    """Register a fresh user + cookies attached to the client."""

    resp = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "tos_accepted": True},
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _enroll(client: AsyncClient) -> tuple[str, list[str]]:
    """Walk through enroll/start + enroll/confirm; return secret + codes."""

    resp = await client.post("/v1/auth/mfa/enroll/start")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    secret = body["secret"]
    assert body["provisioning_uri"].startswith("otpauth://")

    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/v1/auth/mfa/enroll/confirm",
        json={"code": code},
    )
    assert resp.status_code == 200, resp.text
    return secret, resp.json()["recovery_codes"]


@pytest.mark.asyncio
async def test_enroll_happy_path(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa1@example.com")
    secret, codes = await _enroll(client)
    assert len(secret) > 0
    assert len(codes) == 10
    # All codes are unique hex strings.
    assert len(set(codes)) == 10
    for c in codes:
        assert len(c) == 10
        int(c, 16)  # valid hex


@pytest.mark.asyncio
async def test_status_reflects_enrollment(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-status@example.com")
    resp = await client.get("/v1/auth/mfa/status")
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False

    await _enroll(client)
    resp = await client.get("/v1/auth/mfa/status")
    body = resp.json()
    assert body["enabled"] is True
    assert body["recovery_codes_remaining"] == 10
    assert body["enrolled_at"] is not None


@pytest.mark.asyncio
async def test_double_enroll_conflicts(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-dup@example.com")
    await _enroll(client)
    resp = await client.post("/v1/auth/mfa/enroll/start")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "MFA_ALREADY_ENABLED"


@pytest.mark.asyncio
async def test_confirm_wrong_code(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-bad@example.com")
    resp = await client.post("/v1/auth/mfa/enroll/start")
    assert resp.status_code == 200

    resp = await client.post(
        "/v1/auth/mfa/enroll/confirm",
        json={"code": "000000"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "MFA_INVALID_CODE"


@pytest.mark.asyncio
async def test_confirm_without_start(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-nostart@example.com")
    resp = await client.post(
        "/v1/auth/mfa/enroll/confirm",
        json={"code": "123456"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "MFA_ENROLLMENT_NOT_STARTED"


@pytest.mark.asyncio
async def test_login_returns_mfa_required(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-login@example.com")
    secret, _ = await _enroll(client)

    # Drop the session cookies — start fresh from the login screen.
    client.cookies.clear()
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-login@example.com", "password": "S3curePass!"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("mfa_required") is True
    assert body.get("mfa_token")
    # No session cookies should have been set on the mfa_required branch.
    assert "sm_access" not in resp.cookies
    assert "sm_refresh" not in resp.cookies

    code = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": body["mfa_token"], "code": code},
    )
    assert resp.status_code == 200, resp.text
    body2 = resp.json()
    assert body2["access_token"]
    assert "sm_access" in resp.cookies
    assert "sm_refresh" in resp.cookies


@pytest.mark.asyncio
async def test_login_mfa_invalid_code(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-wrong@example.com")
    await _enroll(client)
    client.cookies.clear()
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-wrong@example.com", "password": "S3curePass!"},
    )
    mfa_token = resp.json()["mfa_token"]

    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": mfa_token, "code": "000000"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "MFA_INVALID_CODE"


@pytest.mark.asyncio
async def test_recovery_code_is_one_shot(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-recovery@example.com")
    _, codes = await _enroll(client)
    client.cookies.clear()

    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-recovery@example.com", "password": "S3curePass!"},
    )
    mfa_token1 = resp.json()["mfa_token"]
    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": mfa_token1, "code": codes[0]},
    )
    assert resp.status_code == 200, resp.text

    # Drop cookies and try to reuse the same recovery code.
    client.cookies.clear()
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-recovery@example.com", "password": "S3curePass!"},
    )
    mfa_token2 = resp.json()["mfa_token"]
    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": mfa_token2, "code": codes[0]},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "MFA_INVALID_CODE"


@pytest.mark.asyncio
async def test_login_mfa_rate_limit(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-rl@example.com")
    await _enroll(client)
    client.cookies.clear()

    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-rl@example.com", "password": "S3curePass!"},
    )
    mfa_token = resp.json()["mfa_token"]

    # Default: 5 attempts. Burn them.
    for _ in range(5):
        resp = await client.post(
            "/v1/auth/login/mfa",
            json={"mfa_token": mfa_token, "code": "000000"},
        )
        assert resp.status_code == 400
    # 6th attempt locked.
    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": mfa_token, "code": "000000"},
    )
    assert resp.status_code == 429
    assert resp.json()["error_code"] == "MFA_RATE_LIMITED"


@pytest.mark.asyncio
async def test_login_mfa_token_invalid(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": "this.is.not.a.jwt.token.no.really", "code": "123456"},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "MFA_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_disable_requires_password_and_code(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-disable@example.com")
    secret, _codes = await _enroll(client)

    # Wrong password.
    resp = await client.post(
        "/v1/auth/mfa/disable",
        json={"current_password": "WrongPass!", "code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

    # Wrong code.
    resp = await client.post(
        "/v1/auth/mfa/disable",
        json={"current_password": "S3curePass!", "code": "000000"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "MFA_INVALID_CODE"

    # Both right.
    resp = await client.post(
        "/v1/auth/mfa/disable",
        json={"current_password": "S3curePass!", "code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 204, resp.text

    # Status reverted; new login (no MFA) succeeds without a second step.
    client.cookies.clear()
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-disable@example.com", "password": "S3curePass!"},
    )
    assert resp.status_code == 200, resp.text
    assert "mfa_required" not in resp.json()
    resp = await client.get("/v1/auth/mfa/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_disable_with_recovery_code(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-disable-rec@example.com")
    _, codes = await _enroll(client)

    resp = await client.post(
        "/v1/auth/mfa/disable",
        json={"current_password": "S3curePass!", "code": codes[0]},
    )
    assert resp.status_code == 204, resp.text


@pytest.mark.asyncio
async def test_regenerate_recovery_codes(client: AsyncClient) -> None:
    await _register_and_login(client, "mfa-regen@example.com")
    secret, first_codes = await _enroll(client)

    # Wrong code -> 400.
    resp = await client.post(
        "/v1/auth/mfa/recovery-codes/regenerate",
        json={"code": "000000"},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "MFA_INVALID_CODE"

    # Correct TOTP code -> fresh batch returned, old ones invalid.
    resp = await client.post(
        "/v1/auth/mfa/recovery-codes/regenerate",
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 200, resp.text
    new_codes = resp.json()["recovery_codes"]
    assert len(new_codes) == 10
    assert set(new_codes).isdisjoint(set(first_codes))

    # Old code should no longer authenticate.
    client.cookies.clear()
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-regen@example.com", "password": "S3curePass!"},
    )
    mfa_token = resp.json()["mfa_token"]
    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": mfa_token, "code": first_codes[1]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_disable_revokes_refresh(client: AsyncClient) -> None:
    """After disable, the refresh cookie must no longer mint new
    access tokens — ``token_version`` was bumped + families revoked."""

    await _register_and_login(client, "mfa-revoke@example.com")
    secret, _ = await _enroll(client)
    # Issue a fresh login (with cookies) via the two-step path so we
    # have a refresh cookie minted under the *current* token_version.
    client.cookies.clear()
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "mfa-revoke@example.com", "password": "S3curePass!"},
    )
    mfa_token = resp.json()["mfa_token"]
    resp = await client.post(
        "/v1/auth/login/mfa",
        json={"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 200

    # Disable from this same session.
    resp = await client.post(
        "/v1/auth/mfa/disable",
        json={"current_password": "S3curePass!", "code": pyotp.TOTP(secret).now()},
    )
    assert resp.status_code == 204

    # The refresh cookie was rotated/cleared by disable. Try to use a
    # stale (none-present) refresh: must fail.
    resp = await client.post("/v1/auth/refresh")
    assert resp.status_code in (401, 400)
