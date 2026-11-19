"""FABRIC-3 / FABRIC-7: failed second factors are counted, spend the token,
lock the factor, and alert the operator; failed passwords alone lock a username."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from mfa_helpers import current_code, enroll
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.api_auth import mfa
from apps.api_auth.models import MfaDevice
from apps.commands import notifications

CREDENTIALS = {"username": "lockout-user", "password": "a-long-enough-password"}
LOGIN = "/api/v1/auth/login/"
VERIFY = "/api/v1/auth/mfa/verify/"
PER_TOKEN = settings.FABRIC_MFA_MAX_FAILURES_PER_TOKEN
LOCK_AT = settings.FABRIC_MFA_LOCKOUT_THRESHOLD


@pytest.fixture
def user(db: Any) -> Any:
    created = get_user_model().objects.create_user(**CREDENTIALS)
    created.totp_secret = enroll(created)  # type: ignore[attr-defined]
    return created


def _token(user: Any) -> str:
    token, enrolled = mfa.issue_mfa_token(user, remember_me=False)
    assert enrolled
    return token


def _wrong(secret: str) -> str:
    return str((int(current_code(secret)) + 500_000) % 1_000_000).zfill(6)


def _fail(user: Any) -> mfa.MfaError:
    """One wrong code with a fresh token; returns the refusal."""
    with pytest.raises(mfa.MfaError) as caught:
        mfa.verify(_token(user), _wrong(user.totp_secret))
    return caught.value


def _device(user: Any) -> MfaDevice:
    return MfaDevice.objects.get(user=user)


def test_thresholds_are_sane() -> None:
    assert 3 <= PER_TOKEN <= 5
    assert PER_TOKEN < LOCK_AT <= 10


@pytest.mark.django_db
def test_a_failed_code_is_counted_on_the_device(user: Any) -> None:
    error = _fail(user)

    assert error.code == "mfa_invalid_code"
    assert _device(user).failed_attempts == 1


@pytest.mark.django_db
def test_the_token_is_spent_after_too_many_failures(user: Any) -> None:
    token = _token(user)
    codes = []
    for _ in range(PER_TOKEN):
        with pytest.raises(mfa.MfaError) as caught:
            mfa.verify(token, _wrong(user.totp_secret))
        codes.append(caught.value.code)

    # The last failure already says "start over"; a correct code is too late.
    assert codes == ["mfa_invalid_code"] * (PER_TOKEN - 1) + ["mfa_token_invalid"]
    with pytest.raises(mfa.MfaError) as caught:
        mfa.verify(token, current_code(user.totp_secret))
    assert caught.value.code == "mfa_token_invalid"


@pytest.mark.django_db
def test_a_success_resets_the_counter(user: Any) -> None:
    for _ in range(PER_TOKEN - 1):
        _fail(user)

    mfa.verify(_token(user), current_code(user.totp_secret))

    assert _device(user).failed_attempts == 0


@pytest.mark.django_db
def test_failures_across_tokens_lock_the_second_factor(user: Any) -> None:
    errors = [_fail(user) for _ in range(LOCK_AT)]

    assert errors[-1].code == "mfa_locked"
    device = _device(user)
    assert device.locked_until is not None and device.locked_until > timezone.now()
    # Even the right code is refused while locked, and nothing more is counted.
    MfaDevice.objects.filter(pk=device.pk).update(last_used_step=None)
    with pytest.raises(mfa.MfaError) as caught:
        mfa.verify(_token(user), current_code(user.totp_secret))
    assert caught.value.code == "mfa_locked"
    assert _device(user).failed_attempts == LOCK_AT


@pytest.mark.django_db
def test_a_recovery_code_does_not_bypass_the_lock(user: Any) -> None:
    codes = mfa.regenerate_recovery_codes(user)
    for _ in range(LOCK_AT):
        _fail(user)

    with pytest.raises(mfa.MfaError) as caught:
        mfa.verify(_token(user), codes[0])

    assert caught.value.code == "mfa_locked"


@pytest.mark.django_db
def test_the_lock_expires_after_the_configured_delay(user: Any) -> None:
    for _ in range(LOCK_AT):
        _fail(user)
    MfaDevice.objects.filter(user=user).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )

    mfa.verify(_token(user), current_code(user.totp_secret))

    device = _device(user)
    assert device.locked_until is None
    assert device.failed_attempts == 0


@pytest.mark.django_db
def test_a_failure_after_the_lock_expired_starts_a_new_count(user: Any) -> None:
    for _ in range(LOCK_AT):
        _fail(user)
    MfaDevice.objects.filter(user=user).update(
        locked_until=timezone.now() - timedelta(seconds=1)
    )

    error = _fail(user)

    assert error.code == "mfa_invalid_code"
    device = _device(user)
    assert device.locked_until is None
    assert device.failed_attempts == 1


@pytest.mark.django_db
def test_reset_mfa_lifts_the_lock(user: Any) -> None:
    from django.core.management import call_command

    for _ in range(LOCK_AT):
        _fail(user)

    call_command("reset_mfa", CREDENTIALS["username"], stdout=mock.MagicMock())

    body = APIClient().post(LOGIN, CREDENTIALS, format="json").json()
    assert body["mfa_setup_required"] is True


@pytest.mark.django_db
def test_the_verify_endpoint_reports_the_lock(user: Any) -> None:
    for _ in range(LOCK_AT - 1):
        _fail(user)
    client = APIClient()
    token = client.post(LOGIN, CREDENTIALS, format="json").json()["mfa_token"]

    response = client.post(
        VERIFY, {"mfa_token": token, "code": _wrong(user.totp_secret)}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "mfa_locked"
    assert OutstandingToken.objects.count() == 0


@pytest.mark.django_db
def test_failures_are_committed_even_though_the_request_is_refused(
    user: Any,
) -> None:
    """The refusal must not roll back the counter (ATOMIC_REQUESTS or not)."""
    client = APIClient()
    token = client.post(LOGIN, CREDENTIALS, format="json").json()["mfa_token"]

    client.post(
        VERIFY, {"mfa_token": token, "code": _wrong(user.totp_secret)}, format="json"
    )

    assert _device(user).failed_attempts == 1


# --- Operator alert ------------------------------------------------------------


@pytest.mark.django_db
def test_the_operator_is_alerted_when_a_token_is_spent_and_on_lock(
    user: Any,
) -> None:
    with mock.patch.object(notifications, "notify_mfa_failures") as notify:
        for _ in range(PER_TOKEN - 1):
            _fail(user)
        notify.assert_not_called()

        _fail(user)
        assert notify.call_count == 1
        assert notify.call_args.kwargs["locked"] is False

        for _ in range(LOCK_AT - PER_TOKEN):
            _fail(user)

    assert notify.call_args.kwargs["locked"] is True
    assert notify.call_args.kwargs["failures"] == LOCK_AT


@pytest.mark.django_db
def test_the_lock_is_logged_as_an_error(user: Any) -> None:
    """An ERROR record also reaches Sentry (logging integration)."""
    with mock.patch.object(mfa, "LOGGER") as logger:
        for _ in range(LOCK_AT):
            _fail(user)

    assert logger.warning.call_count >= LOCK_AT - 1
    assert logger.error.call_count == 1
    assert "locked" in logger.error.call_args.args[0]


@pytest.mark.django_db
def test_mfa_alert_goes_through_pushit_without_any_code(
    user: Any, settings: Any
) -> None:
    settings.PUSHIT_ENABLED = True
    settings.PUSHIT_APP_TOKEN = "apt_test-token"
    settings.PUSHIT_BASE_URL = "https://pushit-api.example.com"
    settings.PUSHIT_ALLOWED_HOSTS = ["pushit-api.example.com"]
    settings.PUSHIT_ACTIVE = True
    settings.PUSHIT_EVENTS = {**settings.PUSHIT_DEFAULT_EVENTS}

    with mock.patch.object(notifications, "_send") as send:
        notifications.notify_mfa_failures(user, failures=LOCK_AT, locked=True)

    kwargs = send.call_args.kwargs
    assert kwargs["event"] == "mfa_failures"
    assert kwargs["destination"] is not None
    assert str(LOCK_AT) in kwargs["message"]
    assert settings.PUSHIT_DEFAULT_EVENTS["mfa_failures"] is True


# --- Shared, persistent throttle cache ----------------------------------------


def test_throttles_use_redis_when_redis_is_configured() -> None:
    import importlib

    with mock.patch.dict("os.environ", {"REDIS_URL": "redis://127.0.0.1:6379/5"}):
        module = importlib.reload(importlib.import_module("config.settings"))
        try:
            conf = module.CACHES["default"]
            assert conf["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
            assert conf["LOCATION"] == "redis://127.0.0.1:6379/5"
            # Never collide with the channels_redis keys of the same database.
            assert conf["KEY_PREFIX"]
        finally:
            importlib.reload(importlib.import_module("config.settings"))


# --- FABRIC-7: only failed passwords count per username -------------------------


def _rate(scope: str) -> int:
    rates = cast(dict[str, str], settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    return int(rates[scope].split("/")[0])


@pytest.mark.django_db
def test_successful_password_steps_never_lock_the_username(user: Any) -> None:
    limit = _rate("login_username")
    client = APIClient()

    statuses = [
        client.post(
            LOGIN,
            CREDENTIALS,
            format="json",
            HTTP_X_FORWARDED_FOR=f"198.51.100.{i}",
        ).status_code
        for i in range(limit + 2)
    ]

    assert statuses == [200] * (limit + 2), statuses


@pytest.mark.django_db
def test_failed_passwords_still_lock_the_username(user: Any) -> None:
    limit = _rate("login_username")
    client = APIClient()
    wrong = {**CREDENTIALS, "password": "wrong-password"}

    for i in range(limit):
        assert (
            client.post(
                LOGIN, wrong, format="json", HTTP_X_FORWARDED_FOR=f"198.51.100.{i}"
            ).status_code
            == 400
        )
    # Once the failures are spent, even the right password waits.
    response = client.post(
        LOGIN, CREDENTIALS, format="json", HTTP_X_FORWARDED_FOR="203.0.113.9"
    )
    assert response.status_code == 429


@pytest.mark.django_db
def test_the_per_ip_cap_still_counts_every_attempt(user: Any) -> None:
    limit = _rate("login")
    client = APIClient()

    statuses = [
        client.post(LOGIN, CREDENTIALS, format="json").status_code
        for _ in range(limit + 1)
    ]

    assert statuses[:limit] == [200] * limit, statuses
    assert statuses[-1] == 429, statuses
    cache.clear()
