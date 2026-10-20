"""Drop the obsolete DRF TokenAuthentication table.

`rest_framework.authtoken` is no longer installed (browser auth moved to
SimpleJWT). Its `authtoken_token` table still holds old tokens in clear text
in production: drop it and forget the app's migration / content-type /
permission rows. Every step tolerates an already-clean database.
"""

from django.db import migrations


def drop_authtoken_table(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        sql = "DROP TABLE IF EXISTS authtoken_token CASCADE"
    else:
        sql = "DROP TABLE IF EXISTS authtoken_token"
    schema_editor.execute(sql)


def forget_authtoken_app(apps, schema_editor):
    schema_editor.execute("DELETE FROM django_migrations WHERE app = 'authtoken'")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission.objects.filter(content_type__app_label="authtoken").delete()
    ContentType.objects.filter(app_label="authtoken").delete()


def purge(apps, schema_editor):
    drop_authtoken_table(apps, schema_editor)
    forget_authtoken_app(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("api_auth", "0004_revoke_pre_mfa_sessions"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(purge, migrations.RunPython.noop),
    ]
