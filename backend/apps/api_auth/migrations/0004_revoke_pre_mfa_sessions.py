"""MFA is mandatory from now on: sessions opened before it must not survive.

A refresh token issued by the password-only login would otherwise keep minting
access tokens for up to `JWT_REFRESH_DAYS` without any second factor. Every
outstanding refresh token is blacklisted; everyone signs in again (and enrolls).
"""

from django.db import migrations


def revoke_all_refresh_tokens(apps, schema_editor):
    OutstandingToken = apps.get_model("token_blacklist", "OutstandingToken")
    BlacklistedToken = apps.get_model("token_blacklist", "BlacklistedToken")
    pending = OutstandingToken.objects.filter(blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in pending.iterator()],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("api_auth", "0003_mfa"),
        ("token_blacklist", "0011_linearizes_history"),
    ]

    operations = [
        migrations.RunPython(revoke_all_refresh_tokens, migrations.RunPython.noop),
    ]
