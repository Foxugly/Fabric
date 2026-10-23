from importlib import import_module

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection

migration = import_module("apps.api_auth.migrations.0005_drop_authtoken")


def _tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


@pytest.mark.django_db(transaction=True)
def test_no_authtoken_table_after_migrations() -> None:
    assert "authtoken_token" not in _tables()


@pytest.mark.django_db(transaction=True)
def test_migration_purges_a_legacy_database() -> None:
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE authtoken_token (key varchar(40) PRIMARY KEY)")
        cursor.execute(
            "INSERT INTO django_migrations (app, name, applied) "
            "VALUES ('authtoken', '0001_initial', CURRENT_TIMESTAMP)"
        )
    ct = ContentType.objects.create(app_label="authtoken", model="token")
    Permission.objects.create(
        content_type=ct, codename="add_token", name="Can add token"
    )

    from django.apps import apps

    with connection.schema_editor() as editor:
        migration.purge(apps, editor)

    assert "authtoken_token" not in _tables()
    assert not ContentType.objects.filter(app_label="authtoken").exists()
    assert not Permission.objects.filter(content_type__app_label="authtoken").exists()
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM django_migrations WHERE app = 'authtoken'")
        assert cursor.fetchone()[0] == 0


@pytest.mark.django_db(transaction=True)
def test_migration_is_idempotent() -> None:
    from django.apps import apps

    with connection.schema_editor() as editor:
        migration.purge(apps, editor)
        migration.purge(apps, editor)
