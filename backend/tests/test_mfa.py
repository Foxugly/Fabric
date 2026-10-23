"""Mandatory TOTP second factor on the web login."""

from __future__ import annotations

import time
from io import StringIO
from typing import Any, cast
from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.management import CommandError, call_command
from mfa_helpers import current_code, enroll, login
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from apps.api_auth import mfa
from apps.api_auth.models import MfaDevice, MfaRecoveryCode

CREDENTIALS = {"username": "mfa-user", "password": "a-long-enough-password"}
ORIGIN = "http://127.0.0.1:4200"
COOKIE = "fabric_refresh"
LOGIN = "/api/v1/auth/login/"
SETUP = "/api/v1/auth/mfa/setup/"
CONFIRM = "/api/v1/auth/mfa/setup/confirm/"
VERIFY = "/api/v1/auth/mfa/verify/"
RECOVERY = "/api/v1/auth/mfa/recovery-codes/"


@pytest.fixture
def user(db: Any) -> Any:
    return get_user_model().objects.create_user(**CREDENTIALS)


def _rate(scope: str) -> int:
    rates = cast(dict[str, str], settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    return int(rates[scope].split("/")[0])


def _cookie_client() -> APIClient:
    client = APIClient()
    client.credentials(HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN=ORIGIN)
    return client


def _mfa_token(client: APIClient, **body: Any) -> str:
    response = client.post(LOGIN, {**CREDENTIALS, **body}, format="json")
    assert response.status_code == 200, response.content
    return str(response.json()["mfa_token"])


def _setup_secret(client: APIClient, token: str) -> str:
    response = client.post(SETUP, {"mfa_token": token}, format="json")
    assert response.status_code == 200, response.content
    return str(response.json()["secret"])


def _enrol(client: APIClient) -> tuple[Any, str]:
    token = _mfa_token(client)
    secret = _setup_secret(client, token)
    response = client.post(
        CONFIRM, {"mfa_token": token, "code": current_code(secret)}, format="json"
    )
    return response, secret


# --- RFC 6238 ----------------------------------------------------------------


def test_totp_matches_the_rfc_6238_sha1_vectors() -> None:
    # RFC 6238 appendix B, secret "12345678901234567890", 8 digits.
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert mfa.totp_at(secret, 59 // 30, digits=8) == "94287082"
    assert mfa.totp_at(secret, 1111111109 // 30, digits=8) == "07081804"
    assert mfa.totp_at(secret, 1234567890 // 30, digits=8) == "89005924"


def test_totp_accepts_one_step_of_drift_and_no_more() -> None:
    secret = mfa.generate_secret()
    now = 1_700_000_000.0
    step = mfa.current_step(now)
    for delta in (-1, 0, 1):
        code = mfa.totp_at(secret, step + delta)
        assert mfa.match_totp_step(secret, code, None, now) == step + delta
    for delta in (-2, 2):
        code = mfa.totp_at(secret, step + delta)
        assert mfa.match_totp_step(secret, code, None, now) is None


def test_secret_is_encrypted_with_a_key_derived_from_the_settings(
    settings: Any,
) -> None:
    secret = mfa.generate_secret()
    encrypted = mfa.encrypt_secret(secret)
    assert secret not in encrypted
    assert mfa.decrypt_secret(encrypted) == secret

    settings.FABRIC_MFA_ENCRYPTION_KEY = "a-dedicated-mfa-key"
    with pytest.raises(mfa.MfaError):
        mfa.decrypt_secret(encrypted)
    assert mfa.decrypt_secret(mfa.encrypt_secret(secret)) == secret


# --- Password step -----------------------------------------------------------


@pytest.mark.django_db
def test_login_never_returns_tokens_and_asks_for_enrolment(user: Any) -> None:
    response = _cookie_client().post(LOGIN, CREDENTIALS, format="json")

    assert response.status_code == 200
    body = response.json()
    assert body["mfa_setup_required"] is True
    assert body["mfa_token"]
    assert "access" not in body and "refresh" not in body
    assert COOKIE not in response.cookies
    assert OutstandingToken.objects.count() == 0


@pytest.mark.django_db
def test_login_of_an_enrolled_user_asks_for_the_code(user: Any) -> None:
    enroll(user)

    body = APIClient().post(LOGIN, CREDENTIALS, format="json").json()

    assert body["mfa_required"] is True
    assert "mfa_setup_required" not in body
    assert "access" not in body


@pytest.mark.django_db
def test_wrong_password_gives_no_mfa_token(user: Any) -> None:
    response = APIClient().post(
        LOGIN, {**CREDENTIALS, "password": "nope"}, format="json"
    )

    assert response.status_code == 400
    assert "mfa_token" not in response.json()


# --- Enrolment ---------------------------------------------------------------


@pytest.mark.django_db
def test_setup_returns_a_qr_and_the_same_pending_secret_twice(user: Any) -> None:
    client = APIClient()
    token = _mfa_token(client)

    first = client.post(SETUP, {"mfa_token": token}, format="json").json()
    second = client.post(SETUP, {"mfa_token": token}, format="json").json()

    assert first["secret"] == second["secret"]
    assert first["otpauth_uri"].startswith("otpauth://totp/Fabric:mfa-user?")
    assert "issuer=Fabric" in first["otpauth_uri"]
    assert f"secret={first['secret']}" in first["otpauth_uri"]
    assert first["qr_svg"].startswith("data:image/svg+xml")
    device = MfaDevice.objects.get(user=user)
    assert device.enabled is False
    # Encrypted at rest.
    assert first["secret"] not in device.pending_secret


@pytest.mark.django_db
def test_confirm_enables_mfa_returns_recovery_codes_and_logs_in(user: Any) -> None:
    response, _ = _enrol(APIClient())

    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["mfa_enabled"] is True
    codes = body["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    device = MfaDevice.objects.get(user=user)
    assert device.enabled and device.secret and not device.pending_secret
    stored = list(MfaRecoveryCode.objects.filter(user=user))
    assert len(stored) == 10
    assert not any(row.code_hash in codes for row in stored)


@pytest.mark.django_db
def test_confirm_with_a_wrong_code_enables_nothing(user: Any) -> None:
    client = APIClient()
    token = _mfa_token(client)
    secret = _setup_secret(client, token)
    wrong = str((int(current_code(secret)) + 1) % 1_000_000).zfill(6)

    response = client.post(CONFIRM, {"mfa_token": token, "code": wrong}, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "mfa_invalid_code"
    assert "access" not in response.json()
    assert MfaDevice.objects.get(user=user).enabled is False


@pytest.mark.django_db
def test_confirm_without_setup_is_refused(user: Any) -> None:
    client = APIClient()
    token = _mfa_token(client)

    response = client.post(
        CONFIRM, {"mfa_token": token, "code": "123456"}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "mfa_invalid_code"


@pytest.mark.django_db
def test_an_enrolled_user_cannot_be_re_enrolled_with_the_password(
    user: Any,
) -> None:
    """A stolen password must not be enough to register a new authenticator."""
    enroll(user)
    client = APIClient()
    token = _mfa_token(client)

    setup = client.post(SETUP, {"mfa_token": token}, format="json")
    confirm = client.post(
        CONFIRM, {"mfa_token": token, "code": "123456"}, format="json"
    )

    assert setup.status_code == 400
    assert setup.json()["code"] == "mfa_already_enabled"
    assert "secret" not in setup.json()
    assert confirm.status_code == 400


@pytest.mark.django_db
def test_verify_is_refused_before_enrolment(user: Any) -> None:
    client = APIClient()
    token = _mfa_token(client)

    response = client.post(
        VERIFY, {"mfa_token": token, "code": "123456"}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "mfa_invalid_code"
    assert OutstandingToken.objects.count() == 0


# --- Verify ------------------------------------------------------------------


@pytest.mark.django_db
def test_verify_with_the_current_code_logs_in(user: Any) -> None:
    secret = enroll(user)

    response = login(APIClient(), CREDENTIALS, secret)

    assert response.status_code == 200
    assert response.json()["access"] and response.json()["refresh"]
    assert "recovery_codes" not in response.json()


@pytest.mark.django_db
def test_verify_with_a_wrong_code_issues_no_token(user: Any) -> None:
    secret = enroll(user)
    client = APIClient()
    token = _mfa_token(client)
    wrong = str((int(current_code(secret)) + 1) % 1_000_000).zfill(6)

    response = client.post(VERIFY, {"mfa_token": token, "code": wrong}, format="json")

    assert response.status_code == 400
    assert response.json() == {"code": "mfa_invalid_code", "detail": "Invalid code."}
    assert OutstandingToken.objects.count() == 0


@pytest.mark.django_db
def test_a_totp_time_step_is_accepted_only_once(user: Any) -> None:
    secret = enroll(user)
    client = APIClient()
    code = current_code(secret)

    first = client.post(
        VERIFY, {"mfa_token": _mfa_token(client), "code": code}, format="json"
    )
    replay = client.post(
        VERIFY, {"mfa_token": _mfa_token(client), "code": code}, format="json"
    )

    assert first.status_code == 200
    assert replay.status_code == 400
    assert replay.json()["code"] == "mfa_invalid_code"


@pytest.mark.django_db
def test_an_older_step_is_refused_once_a_newer_one_was_used(user: Any) -> None:
    secret = enroll(user)
    step = mfa.current_step()
    MfaDevice.objects.filter(user=user).update(last_used_step=step)
    client = APIClient()

    response = client.post(
        VERIFY,
        {"mfa_token": _mfa_token(client), "code": mfa.totp_at(secret, step - 1)},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_mfa_token_completes_a_single_login(user: Any) -> None:
    secret = enroll(user)
    client = APIClient()
    token = _mfa_token(client)
    step = mfa.current_step()

    first = client.post(
        VERIFY, {"mfa_token": token, "code": mfa.totp_at(secret, step)}, format="json"
    )
    again = client.post(
        VERIFY,
        {"mfa_token": token, "code": mfa.totp_at(secret, step + 1)},
        format="json",
    )

    assert first.status_code == 200
    assert again.status_code == 400
    assert again.json()["code"] == "mfa_token_invalid"


@pytest.mark.django_db
def test_a_new_password_step_invalidates_the_previous_token(user: Any) -> None:
    secret = enroll(user)
    client = APIClient()
    old = _mfa_token(client)
    _mfa_token(client)

    response = client.post(
        VERIFY, {"mfa_token": old, "code": current_code(secret)}, format="json"
    )

    assert response.json()["code"] == "mfa_token_invalid"


@pytest.mark.django_db
def test_an_expired_mfa_token_is_refused(user: Any) -> None:
    secret = enroll(user)
    client = APIClient()
    token = _mfa_token(client)

    later = time.time() + settings.FABRIC_MFA_TOKEN_TTL_SECONDS + 1
    with mock.patch("django.core.signing.time.time", return_value=later):
        response = client.post(
            VERIFY, {"mfa_token": token, "code": current_code(secret)}, format="json"
        )

    assert response.status_code == 400
    assert response.json()["code"] == "mfa_token_invalid"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "forge",
    [
        lambda token, user: token[:-2] + ("AA" if not token.endswith("AA") else "BB"),
        lambda token, user: signing.dumps(
            {"u": user.pk, "n": "guess", "r": False}, salt="fabric.api_auth.mfa_token"
        ),
        lambda token, user: signing.dumps(
            {"u": user.pk, "n": MfaDevice.objects.get(user=user).nonce, "r": False},
            salt="another-salt",
        ),
        lambda token, user: "not-a-token",
    ],
)
def test_a_forged_mfa_token_is_refused(user: Any, forge: Any) -> None:
    secret = enroll(user)
    client = APIClient()
    token = _mfa_token(client)

    response = client.post(
        VERIFY,
        {"mfa_token": forge(token, user), "code": current_code(secret)},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "mfa_token_invalid"
    assert OutstandingToken.objects.count() == 0


@pytest.mark.django_db
def test_an_inactive_user_cannot_complete_the_login(user: Any) -> None:
    secret = enroll(user)
    client = APIClient()
    token = _mfa_token(client)
    user.is_active = False
    user.save(update_fields=["is_active"])

    response = client.post(
        VERIFY, {"mfa_token": token, "code": current_code(secret)}, format="json"
    )

    assert response.json()["code"] == "mfa_token_invalid"


# --- Recovery codes ----------------------------------------------------------


@pytest.mark.django_db
def test_a_recovery_code_works_exactly_once(user: Any) -> None:
    enroll(user)
    codes = mfa.regenerate_recovery_codes(user)
    client = APIClient()

    first = client.post(
        VERIFY,
        {"mfa_token": _mfa_token(client), "code": codes[0].lower()},
        format="json",
    )
    again = client.post(
        VERIFY, {"mfa_token": _mfa_token(client), "code": codes[0]}, format="json"
    )

    assert first.status_code == 200
    assert first.json()["access"]
    assert again.status_code == 400
    assert again.json()["code"] == "mfa_invalid_code"
    assert MfaRecoveryCode.objects.filter(user=user, used_at__isnull=True).count() == 9


@pytest.mark.django_db
def test_recovery_codes_can_be_regenerated_with_the_password(user: Any) -> None:
    enroll(user)
    old = mfa.regenerate_recovery_codes(user)
    client = APIClient()
    client.force_authenticate(user=user)

    wrong = client.post(RECOVERY, {"password": "nope"}, format="json")
    response = client.post(
        RECOVERY, {"password": CREDENTIALS["password"]}, format="json"
    )

    assert wrong.status_code == 400
    assert wrong.json()["code"] == "password_invalid"
    assert response.status_code == 200
    new = response.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    assert MfaRecoveryCode.objects.filter(user=user).count() == 10
    # The old codes are gone.
    anon = APIClient()
    replay = anon.post(
        VERIFY, {"mfa_token": _mfa_token(anon), "code": old[0]}, format="json"
    )
    assert replay.status_code == 400


@pytest.mark.django_db
def test_recovery_codes_endpoint_requires_authentication(user: Any) -> None:
    response = APIClient().post(
        RECOVERY, {"password": CREDENTIALS["password"]}, format="json"
    )

    assert response.status_code == 401


# --- Throttling --------------------------------------------------------------


@pytest.mark.django_db
def test_verify_is_throttled_per_ip(user: Any) -> None:
    enroll(user)
    limit = _rate("mfa")
    client = APIClient()
    token = _mfa_token(client)

    statuses = [
        client.post(
            VERIFY, {"mfa_token": token, "code": "000000"}, format="json"
        ).status_code
        for _ in range(limit + 1)
    ]

    assert statuses[-1] == 429, statuses


@pytest.mark.django_db
def test_verify_is_throttled_per_account_whatever_the_ip(user: Any) -> None:
    enroll(user)
    limit = _rate("mfa_user")
    client = APIClient()
    token = _mfa_token(client)

    statuses = [
        client.post(
            VERIFY,
            {"mfa_token": token, "code": "000000"},
            format="json",
            HTTP_X_FORWARDED_FOR=f"198.51.100.{i}",
        ).status_code
        for i in range(limit + 1)
    ]

    assert statuses[:limit] == [400] * limit, statuses
    assert statuses[-1] == 429, statuses


@pytest.mark.django_db
def test_confirm_is_throttled_too(user: Any) -> None:
    limit = _rate("mfa")
    client = APIClient()
    token = _mfa_token(client)
    _setup_secret(client, token)

    statuses = [
        client.post(
            CONFIRM, {"mfa_token": token, "code": "000000"}, format="json"
        ).status_code
        for _ in range(limit + 1)
    ]

    assert statuses[-1] == 429, statuses


# --- Cookie mode -------------------------------------------------------------


@pytest.mark.django_db
def test_cookie_mode_verify_sets_the_httponly_cookie(user: Any) -> None:
    secret = enroll(user)

    response = login(_cookie_client(), CREDENTIALS, secret)

    assert response.status_code == 200
    assert response.json()["access"]
    assert "refresh" not in response.json()
    cookie = response.cookies[COOKIE]
    assert cookie["httponly"]
    assert cookie["path"] == "/api/v1/auth/"
    assert cookie["max-age"] == ""  # no "remember me": browser-session cookie


@pytest.mark.django_db
def test_cookie_mode_remember_me_survives_the_mfa_step(user: Any) -> None:
    secret = enroll(user)

    response = login(
        _cookie_client(), {**CREDENTIALS, "remember_me": True}, secret
    )

    assert response.cookies[COOKIE]["max-age"] != ""


@pytest.mark.django_db
def test_cookie_mode_confirm_sets_the_cookie_and_returns_codes(user: Any) -> None:
    response, _ = _enrol(_cookie_client())

    assert response.status_code == 200
    assert "refresh" not in response.json()
    assert len(response.json()["recovery_codes"]) == 10
    assert response.cookies[COOKIE]["httponly"]


@pytest.mark.django_db
@pytest.mark.parametrize("path", [SETUP, CONFIRM, VERIFY])
def test_cookie_mode_mfa_steps_refuse_a_foreign_origin(user: Any, path: str) -> None:
    client = _cookie_client()
    token = _mfa_token(client)
    client.credentials(HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN="https://evil.example")

    response = client.post(path, {"mfa_token": token, "code": "123456"}, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


# --- Account lifecycle -------------------------------------------------------


@pytest.mark.django_db
def test_me_exposes_mfa_enabled(user: Any) -> None:
    client = APIClient()
    client.force_authenticate(user=user)
    assert client.get("/api/v1/auth/me/").json()["user"]["mfa_enabled"] is False

    enroll(user)

    assert client.get("/api/v1/auth/me/").json()["user"]["mfa_enabled"] is True


@pytest.mark.django_db
def test_password_change_keeps_mfa(user: Any) -> None:
    enroll(user)
    mfa.regenerate_recovery_codes(user)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        "/api/v1/auth/password/change/",
        {
            "current_password": CREDENTIALS["password"],
            "new_password": "another-long-passphrase",
        },
        format="json",
    )

    assert response.status_code == 200
    assert MfaDevice.objects.get(user=user).enabled is True
    assert MfaRecoveryCode.objects.filter(user=user).count() == 10


@pytest.mark.django_db
def test_account_deletion_deletes_mfa_data(user: Any) -> None:
    enroll(user)
    mfa.regenerate_recovery_codes(user)
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.delete(
        "/api/v1/auth/me/", {"password": CREDENTIALS["password"]}, format="json"
    )

    assert response.status_code == 204
    assert MfaDevice.objects.count() == 0
    assert MfaRecoveryCode.objects.count() == 0


# --- reset_mfa ---------------------------------------------------------------


@pytest.mark.django_db
def test_reset_mfa_clears_the_factor_and_revokes_sessions(user: Any) -> None:
    secret = enroll(user)
    mfa.regenerate_recovery_codes(user)
    login(APIClient(), CREDENTIALS, secret)
    assert OutstandingToken.objects.filter(user=user).count() == 1
    out = StringIO()

    call_command("reset_mfa", CREDENTIALS["username"], stdout=out)

    assert "MFA reset" in out.getvalue()
    assert not MfaDevice.objects.filter(user=user).exists()
    assert not MfaRecoveryCode.objects.filter(user=user).exists()
    assert BlacklistedToken.objects.filter(token__user=user).count() == 1
    # The next login enrolls again.
    body = APIClient().post(LOGIN, CREDENTIALS, format="json").json()
    assert body["mfa_setup_required"] is True


@pytest.mark.django_db
def test_reset_mfa_of_an_unknown_user_fails() -> None:
    with pytest.raises(CommandError):
        call_command("reset_mfa", "nobody")
