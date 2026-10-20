"""Refresh-token revocation shared by logout, password change and the signal."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import pre_save
from django.dispatch import receiver

LOGGER = logging.getLogger(__name__)


def revoke_refresh_tokens(user_id: Any) -> int:
    """Blacklist every refresh token issued to the user so far.

    Access tokens already issued stay valid until they expire
    (`JWT_ACCESS_MINUTES`): they are stateless by design.
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    pending = list(
        OutstandingToken.objects.filter(
            user_id=user_id, blacklistedtoken__isnull=True
        )
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in pending],
        ignore_conflicts=True,
    )
    return len(pending)


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def _revoke_sessions_on_password_change(
    sender: Any, instance: Any, **kwargs: Any
) -> None:
    """A new password signs every existing session out, whatever the path.

    The API endpoint, `manage.py changepassword` and the admin all end in a
    `save()` with a new hash: hooking the save covers them all. The endpoint
    mints its fresh refresh token *after* the save, so it survives the sweep.
    """
    if instance.pk is None or kwargs.get("raw"):
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "password" not in update_fields:
        return
    previous = (
        get_user_model()
        .objects.filter(pk=instance.pk)
        .values_list("password", flat=True)
        .first()
    )
    if previous is None or previous == instance.password:
        return
    revoked = revoke_refresh_tokens(instance.pk)
    LOGGER.info("password changed: %s refresh token(s) revoked", revoked)
