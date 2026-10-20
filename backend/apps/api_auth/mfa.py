"""Mandatory TOTP second factor for the web login.

Flow: `auth/login/` checks the password and hands out a short-lived, signed
`mfa_token` instead of JWTs; `auth/mfa/setup/` + `auth/mfa/setup/confirm/`
enrol a not-yet-enrolled user, `auth/mfa/verify/` checks a TOTP or recovery
code. Only the last two ever mint tokens, so no JWT exists without a verified
second factor.

* TOTP is RFC 6238 (HMAC-SHA1, 6 digits, 30 s steps, ±1 step of drift): what
  every authenticator app understands. A time-step is accepted once per user
  (`MfaDevice.last_used_step`), so an observed code cannot be replayed.
* Secrets are encrypted at rest with Fernet. The key is derived with
  HKDF-SHA256 from `FABRIC_MFA_ENCRYPTION_KEY` when set, else from
  `SECRET_KEY`.
* `mfa_token` = `django.core.signing` (salted, timestamped, 5 min) over the
  user id and the device nonce. The nonce rotates whenever a login completes
  (and at every password step), so a token completes at most one login.
* Recovery codes: 10 per user, shown once, stored as salted SHA-256 (they are
  random, 50 bits each: a slow hash would only slow the login down).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.api_auth.models import MfaDevice, MfaRecoveryCode

TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
#: Accepted drift, in steps, on either side of the current one.
TOTP_DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
_RECOVERY_CODE_LENGTH = 10
_TOKEN_SALT = "fabric.api_auth.mfa_token"


class MfaError(Exception):
    """A refused MFA step. `code` is the generic, client-facing error code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def token_invalid() -> MfaError:
    return MfaError("mfa_token_invalid", "Sign-in expired: enter your password again.")


def code_invalid() -> MfaError:
    return MfaError("mfa_invalid_code", "Invalid code.")


# --- Encryption at rest ------------------------------------------------------


def _fernet() -> Fernet:
    source = str(settings.FABRIC_MFA_ENCRYPTION_KEY or settings.SECRET_KEY)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"fabric.mfa",
        info=b"totp-secret-v1",
    ).derive(source.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        # The key changed since enrolment: only `reset_mfa` can recover.
        raise code_invalid() from exc


# --- RFC 6238 TOTP -----------------------------------------------------------


def generate_secret() -> str:
    """160-bit secret (the RFC 4226 recommendation), base32 without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // TOTP_PERIOD_SECONDS)


def totp_at(secret: str, step: int, digits: int = TOTP_DIGITS) -> str:
    digest = hmac.new(_secret_bytes(secret), struct.pack(">Q", step), "sha1").digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def match_totp_step(
    secret: str, code: str, last_used_step: int | None, now: float | None = None
) -> int | None:
    """The time-step `code` belongs to, if valid and newer than the last one."""
    code = code.strip().replace(" ", "")
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - TOTP_DRIFT_STEPS, step + TOTP_DRIFT_STEPS + 1):
        if last_used_step is not None and candidate <= last_used_step:
            continue
        if hmac.compare_digest(totp_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, username: str) -> str:
    issuer = str(settings.FABRIC_MFA_ISSUER)
    label = quote(f"{issuer}:{username}", safe=":@")
    params = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{params}"


def qr_svg_data_uri(uri: str) -> str:
    import segno

    return str(segno.make(uri, error="m").svg_data_uri(scale=5, border=2))


# --- mfa_token ---------------------------------------------------------------


def _new_nonce() -> str:
    return secrets.token_urlsafe(24)


def issue_mfa_token(user: Any, *, remember_me: bool) -> tuple[str, bool]:
    """Password step passed: a fresh token, and whether MFA is already enrolled.

    Rotating the nonce here keeps only the latest token of a user valid.
    """
    nonce = _new_nonce()
    device, created = MfaDevice.objects.get_or_create(
        user=user, defaults={"nonce": nonce}
    )
    if not created:
        device.nonce = nonce
        device.save(update_fields=["nonce", "updated_at"])
    token = signing.dumps(
        {"u": user.pk, "n": nonce, "r": bool(remember_me)}, salt=_TOKEN_SALT
    )
    return token, device.enabled


def _load_token(token: str) -> dict[str, Any]:
    try:
        payload = signing.loads(
            token, salt=_TOKEN_SALT, max_age=settings.FABRIC_MFA_TOKEN_TTL_SECONDS
        )
    except signing.BadSignature as exc:  # SignatureExpired is a subclass
        raise token_invalid() from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("n"), str):
        raise token_invalid()
    return payload


def peek_token_user_id(token: str) -> Any:
    """User id of a valid-looking token (throttle key); None otherwise."""
    try:
        return _load_token(token).get("u")
    except MfaError:
        return None


@dataclass
class PendingLogin:
    device: MfaDevice
    user: Any
    remember_me: bool


def _locked_pending_login(token: str) -> PendingLogin:
    """Resolve the token to its device, locked. Call inside `transaction.atomic`."""
    payload = _load_token(token)
    user_id = payload.get("u")
    if not isinstance(user_id, int):
        raise token_invalid()
    device = MfaDevice.objects.select_for_update().filter(user_id=user_id).first()
    if device is None or not hmac.compare_digest(device.nonce, payload["n"]):
        raise token_invalid()
    user = get_user_model().objects.filter(pk=device.user_id, is_active=True).first()
    if user is None:
        raise token_invalid()
    return PendingLogin(device=device, user=user, remember_me=bool(payload.get("r")))


def pending_setup(token: str) -> tuple[Any, str]:
    """`mfa/setup/`: the user and their pending secret (created on first call)."""
    with transaction.atomic():
        pending = _locked_pending_login(token)
        device = pending.device
        if device.enabled:
            raise MfaError("mfa_already_enabled", "MFA is already set up.")
        if device.pending_secret:
            secret = decrypt_secret(device.pending_secret)
        else:
            secret = generate_secret()
            device.pending_secret = encrypt_secret(secret)
            device.save(update_fields=["pending_secret", "updated_at"])
        return pending.user, secret


def confirm_setup(token: str, code: str) -> tuple[Any, bool, list[str]]:
    """`mfa/setup/confirm/`: enable MFA. Returns (user, remember_me, codes)."""
    with transaction.atomic():
        pending = _locked_pending_login(token)
        device = pending.device
        if device.enabled:
            raise MfaError("mfa_already_enabled", "MFA is already set up.")
        if not device.pending_secret:
            raise code_invalid()
        secret = decrypt_secret(device.pending_secret)
        step = match_totp_step(secret, code, device.last_used_step)
        if step is None:
            raise code_invalid()
        device.secret = device.pending_secret
        device.pending_secret = ""
        device.enabled = True
        device.confirmed_at = timezone.now()
        device.last_used_step = step
        device.nonce = _new_nonce()  # the token is spent
        device.save()
        codes = regenerate_recovery_codes(pending.user)
        return pending.user, pending.remember_me, codes


def verify(token: str, code: str) -> tuple[Any, bool]:
    """`mfa/verify/`: a TOTP code or a recovery code. Returns (user, remember_me)."""
    with transaction.atomic():
        pending = _locked_pending_login(token)
        device = pending.device
        if not device.enabled or not device.secret:
            raise code_invalid()
        step = match_totp_step(
            decrypt_secret(device.secret), code, device.last_used_step
        )
        if step is not None:
            device.last_used_step = step
        elif not _consume_recovery_code(pending.user, code):
            raise code_invalid()
        device.nonce = _new_nonce()  # the token is spent
        device.save(update_fields=["last_used_step", "nonce", "updated_at"])
        return pending.user, pending.remember_me


# --- Recovery codes ----------------------------------------------------------


def _normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _hash_recovery_code(salt: str, code: str) -> str:
    material = f"{salt}:{_normalize_recovery_code(code)}".encode()
    return hashlib.sha256(material).hexdigest()


def _new_recovery_code() -> str:
    raw = "".join(
        secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_CODE_LENGTH)
    )
    half = _RECOVERY_CODE_LENGTH // 2
    return f"{raw[:half]}-{raw[half:]}"


def regenerate_recovery_codes(user: Any) -> list[str]:
    """Replace every recovery code of the user; the clear codes are returned once."""
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    with transaction.atomic():
        MfaRecoveryCode.objects.filter(user=user).delete()
        rows = []
        for code in codes:
            salt = secrets.token_hex(16)
            rows.append(
                MfaRecoveryCode(
                    user=user, salt=salt, code_hash=_hash_recovery_code(salt, code)
                )
            )
        MfaRecoveryCode.objects.bulk_create(rows)
    return codes


def _consume_recovery_code(user: Any, code: str) -> bool:
    if len(_normalize_recovery_code(code)) != _RECOVERY_CODE_LENGTH:
        return False
    for row in MfaRecoveryCode.objects.filter(user=user, used_at__isnull=True):
        if hmac.compare_digest(row.code_hash, _hash_recovery_code(row.salt, code)):
            # Conditional update: of two concurrent uses, one wins.
            return (
                MfaRecoveryCode.objects.filter(
                    pk=row.pk, used_at__isnull=True
                ).update(used_at=timezone.now())
                == 1
            )
    return False


def reset(user: Any) -> None:
    """Forget the user's second factor: the next login enrolls again."""
    with transaction.atomic():
        MfaDevice.objects.filter(user=user).delete()
        MfaRecoveryCode.objects.filter(user=user).delete()


def is_enabled(user: Any) -> bool:
    return MfaDevice.objects.filter(user_id=user.pk, enabled=True).exists()
