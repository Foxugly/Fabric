"""Sign in through the mandatory TOTP step in tests."""

from __future__ import annotations

from typing import Any

from rest_framework.test import APIClient

from apps.api_auth import mfa
from apps.api_auth.models import MfaDevice


def enroll(user: Any) -> str:
    """Enable MFA for `user` directly; returns the clear base32 secret."""
    secret = mfa.generate_secret()
    MfaDevice.objects.update_or_create(
        user=user,
        defaults={
            "secret": mfa.encrypt_secret(secret),
            "enabled": True,
            "nonce": "unused",
        },
    )
    return secret


def current_code(secret: str) -> str:
    return mfa.totp_at(secret, mfa.current_step())


def login(
    client: APIClient, credentials: dict[str, Any], secret: str, **extra: Any
) -> Any:
    """Password step + TOTP step; returns the response of the second one.

    Several logins in one test land in the same 30 s step: the replay guard is
    reset first, it has tests of its own.
    """
    MfaDevice.objects.filter(user__username=credentials["username"]).update(
        last_used_step=None
    )
    first = client.post("/api/v1/auth/login/", credentials, format="json", **extra)
    assert first.status_code == 200, first.content
    assert first.json().get("mfa_required") is True, first.json()
    return client.post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": first.json()["mfa_token"], "code": current_code(secret)},
        format="json",
        **extra,
    )
