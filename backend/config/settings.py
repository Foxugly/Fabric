from __future__ import annotations

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

# Settings are the first module every entry point loads (manage.py, asgi, wsgi,
# pytest-django), so this is the only place that reliably makes the monorepo's
# `shared` package importable before Django loads the app registry.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


#: Fleet env selector (OPERATIONS.md §3.14): `PROD` or `DEV`.
STATE = _env("STATE", "DEV").upper()
DEBUG = _env_bool("DEBUG", STATE != "PROD")

SECRET_KEY = _env("SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off")
    SECRET_KEY = "dev-secret-key"

ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "channels",
    "apps.agents",
    "apps.api_auth",
    "apps.commands",
    "apps.conversations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

# Database — fleet DB_* 6-var convention (OPERATIONS.md §3.13). No DB_ENGINE
# means sqlite, which is the local development and test path.
DB_ENGINE_ALIASES = {
    "postgresql": "django.db.backends.postgresql",
    "postgres": "django.db.backends.postgresql",
    "sqlite3": "django.db.backends.sqlite3",
    "sqlite": "django.db.backends.sqlite3",
}
_db_engine = _env("DB_ENGINE")
if _db_engine:
    DATABASES = {
        "default": {
            "ENGINE": DB_ENGINE_ALIASES.get(_db_engine, _db_engine),
            "NAME": _env("DB_NAME", "fabric"),
            "USER": _env("DB_USER", "fabric"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": _env("DB_HOST", "127.0.0.1"),
            "PORT": _env("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = str(BASE_DIR / "staticfiles")

# Channels — Redis is mandatory as soon as more than one process is involved,
# because command dispatch and permission requests travel through the layer
# (OPERATIONS.md §3.11, ASGI section). Fabric uses Redis DB 5.
REDIS_URL = _env("REDIS_URL")
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        }
    }
elif not DEBUG:
    raise ImproperlyConfigured(
        "REDIS_URL is required when DEBUG is off: the in-memory channel layer "
        "does not carry commands between processes"
    )
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api_auth.authentication.ExpiringTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # Login is the only barrier between the Internet and remote code execution
    # on the operator's PC, so it is rate limited by IP.
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "login": _env("THROTTLE_LOGIN", "10/hour"),
    },
}

CORS_ALLOWED_ORIGINS = _env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://127.0.0.1:4200,http://localhost:4200",
)

#: An agent grants remote code execution on the machine it runs on. Outside debug
#: Fabric is assumed reachable from the Internet, so transport security is
#: mandatory rather than optional.
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    X_FRAME_OPTIONS = "DENY"

LOG_LEVEL = _env("LOG_LEVEL", _env("FABRIC_LOG_LEVEL", "INFO")).upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "fabric": {"format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "fabric"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "apps": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

#: A command whose agent never reports back is timed out this long after its own
#: `timeout_seconds` has elapsed.
FABRIC_COMMAND_GRACE_SECONDS = int(_env("FABRIC_COMMAND_GRACE_SECONDS", "30"))

#: How long an issued API token stays valid. A token is a bearer credential for
#: an agent that can run code, so it must not live forever.
FABRIC_TOKEN_TTL_HOURS = int(_env("FABRIC_TOKEN_TTL_HOURS", "168"))

# PushIT notifications — Fabric is meant to be driven while away from the
# screen, and a turn blocked on an approval is invisible until you look. PushIT
# (the fleet's own push service) closes that gap.
#
# `PUSHIT_EVENTS` is a JSON object so the policy can change without a deploy.
# Defaults are scoped to Claude on purpose: a PowerShell `git status` finishing
# does not deserve a phone buzz, an approval request does.
PUSHIT_ENABLED = _env_bool("PUSHIT_ENABLED", False)
PUSHIT_BASE_URL = _env("PUSHIT_BASE_URL", "https://pushit-api.foxugly.com").rstrip("/")
PUSHIT_APP_TOKEN = _env("PUSHIT_APP_TOKEN")
PUSHIT_TIMEOUT_SECONDS = int(_env("PUSHIT_TIMEOUT_SECONDS", "5"))

PUSHIT_DEFAULT_EVENTS = {
    "permission_request": True,
    "claude_turn_completed": True,
    "claude_turn_failed": True,
    "agent_offline": False,
}


def _pushit_events() -> dict[str, bool]:
    raw = _env("PUSHIT_EVENTS")
    events = dict(PUSHIT_DEFAULT_EVENTS)
    if not raw:
        return events
    import json

    try:
        configured = json.loads(raw)
    except ValueError:
        # A malformed policy must not silently disable every notification, nor
        # crash the site: keep the defaults and say so.
        import logging

        logging.getLogger("apps.commands").warning(
            "PUSHIT_EVENTS is not valid JSON, falling back to the defaults"
        )
        return events
    if isinstance(configured, dict):
        for name in events:
            if name in configured:
                events[name] = bool(configured[name])
    return events


PUSHIT_EVENTS = _pushit_events()

#: Sending needs the switch AND a token: a token alone must not start pushing,
#: and the switch alone has nothing to push with.
PUSHIT_ACTIVE = bool(PUSHIT_ENABLED and PUSHIT_APP_TOKEN and PUSHIT_BASE_URL)

# Sentry (OPERATIONS.md §3.8).
SENTRY_DSN = _env("SENTRY_DSN")

#: A DSN alone must NOT be enough to start reporting. A production DSN copied
#: into a developer's `.env` would otherwise send every local crash into the
#: production project, drowning real incidents in noise — exactly what happened
#: to pushit-backend (31 events from a Windows dev box). Reporting therefore
#: requires being in PROD, or an explicit opt-in for deliberate local testing.
SENTRY_ENABLED = bool(SENTRY_DSN) and (
    STATE == "PROD" or _env_bool("SENTRY_ENABLE", False)
)

if SENTRY_ENABLED:  # pragma: no cover - exercised in production only
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=_env("SENTRY_ENVIRONMENT", STATE),
        release=_env("SENTRY_RELEASE") or None,
        traces_sample_rate=float(_env("SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,
    )
