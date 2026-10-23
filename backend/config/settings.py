from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from corsheaders.defaults import default_headers
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
    "rest_framework_simplejwt.token_blacklist",
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
            # 0 under daphne (OPERATIONS.md §3.22): persistent connections are
            # for gunicorn only. In ASGI every sync_to_async thread would keep
            # its own connection open, and PostgreSQL runs out of slots.
            "CONN_MAX_AGE": 0,
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
    # SimpleJWT only (OPERATIONS.md §3.25): a short-lived access token kept in
    # the SPA's memory, the refresh token in an HttpOnly cookie
    # (apps/api_auth/refresh_cookie.py). No SessionAuthentication: no API view
    # is reachable through the admin's session cookie, so CSRF never applies.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # Every caller is throttled, signed in or not: a stolen access token must
    # not be able to queue commands without bound.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": _env("THROTTLE_ANON", "60/min"),
        "user": _env("THROTTLE_USER", "300/min"),
        # Login is the only barrier between the Internet and remote code
        # execution on the operator's PC: rate limited per IP AND per username.
        "login": _env("THROTTLE_LOGIN", "10/hour"),
        "login_username": _env("THROTTLE_LOGIN_USERNAME", "20/hour"),
        "password_change": _env("THROTTLE_PASSWORD_CHANGE", "5/min"),
        "account_delete": _env("THROTTLE_ACCOUNT_DELETE", "5/min"),
        "ws_ticket": _env("THROTTLE_WS_TICKET", "30/min"),
        # TOTP second factor: a 6-digit code is guessable, so verify/confirm
        # are bounded per client IP AND per account (the one the mfa_token names).
        "mfa": _env("THROTTLE_MFA", "5/min"),
        "mfa_user": _env("THROTTLE_MFA_USER", "5/min"),
        "mfa_recovery_codes": _env("THROTTLE_MFA_RECOVERY_CODES", "5/min"),
    },
    # nginx is the single reverse proxy: trust only the last X-Forwarded-For
    # entry (the peer nginx saw), never what the client put there itself.
    # Without this DRF keys anonymous throttles on the whole client-controlled
    # header, and a forged prefix per request defeats the login throttle.
    "NUM_PROXIES": 1,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(_env("JWT_ACCESS_MINUTES", "15"))),
    # Sliding window: every refresh rotates the token and blacklists the old
    # one. Shorter than PushIT's 365 days on purpose — a Fabric session drives
    # code execution on the operator's PC.
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(_env("JWT_REFRESH_DAYS", "7"))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

CORS_ALLOWED_ORIGINS = _env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://127.0.0.1:4200,http://localhost:4200",
)
# Credentials (the refresh cookie) are only ever allowed for the explicit list
# above — never with a wildcard. This list is also the server-side Origin
# allow-list of cookie mode.
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = False
if "*" in CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured(
        "CORS_ALLOWED_ORIGINS must list explicit origins (credentials are allowed)"
    )
# `x-auth-mode`: the SPA's opt-in to the HttpOnly refresh cookie.
CORS_ALLOW_HEADERS = (*default_headers, "sentry-trace", "baggage", "x-auth-mode")

# HttpOnly refresh cookie (web SPA only, opt-in via `X-Auth-Mode: cookie`).
AUTH_REFRESH_COOKIE_NAME = _env("AUTH_REFRESH_COOKIE_NAME", "fabric_refresh")
AUTH_REFRESH_COOKIE_PATH = "/api/v1/auth/"
AUTH_REFRESH_COOKIE_SECURE = _env_bool("AUTH_REFRESH_COOKIE_SECURE", True)

#: Lifetime of the single-use ticket that opens the browser event socket.
#: Browsers cannot set headers on a WebSocket, so the ticket travels in the
#: URL; being single-use and short-lived is what keeps it out of harm's way in
#: access logs.
FABRIC_WS_TICKET_TTL_SECONDS = min(int(_env("FABRIC_WS_TICKET_TTL_SECONDS", "30")), 30)

#: Mandatory TOTP second factor (web login). The TOTP secrets are encrypted at
#: rest with Fernet; the key is derived with HKDF-SHA256 from
#: `FABRIC_MFA_ENCRYPTION_KEY` when set, else from `SECRET_KEY`. Changing the
#: source key makes every enrolled secret unreadable: run `reset_mfa` for each
#: user, who then re-enrolls at the next sign-in.
FABRIC_MFA_ENCRYPTION_KEY = _env("FABRIC_MFA_ENCRYPTION_KEY")
#: Lifetime of the `mfa_token` handed out by the password step.
FABRIC_MFA_TOKEN_TTL_SECONDS = 300
FABRIC_MFA_ISSUER = "Fabric"

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

#: Agent socket limits (M1): a frame above the size closes the socket (1009),
#: so does a burst above the per-minute message count (1008). Claude streams
#: one progress frame per delta, hence the generous rate.
FABRIC_AGENT_MAX_MESSAGE_BYTES = int(
    _env("FABRIC_AGENT_MAX_MESSAGE_BYTES", str(1024 * 1024))
)
FABRIC_AGENT_MAX_MESSAGES_PER_MINUTE = int(
    _env("FABRIC_AGENT_MAX_MESSAGES_PER_MINUTE", "3000")
)

#: Output a command may store (progress deltas, then its result), in
#: characters; beyond it the text is cut and ends with a marker (I4).
FABRIC_COMMAND_OUTPUT_MAX_CHARS = int(
    _env("FABRIC_COMMAND_OUTPUT_MAX_CHARS", "200000")
)

#: Days of command output, events, approvals and messages kept before the daily
#: purge (`manage.py purge_fabric_history`, systemd fabric-purge.timer).
FABRIC_RETENTION_DAYS = max(int(_env("FABRIC_RETENTION_DAYS", "30")), 1)

#: A command whose agent never reports back is timed out this long after its own
#: `timeout_seconds` has elapsed.
FABRIC_COMMAND_GRACE_SECONDS = int(_env("FABRIC_COMMAND_GRACE_SECONDS", "30"))

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
#: The only hosts an app token is ever POSTed to (https only, no redirects):
#: a target's `base_url` is user input, so anything else is an SSRF (M3).
PUSHIT_ALLOWED_HOSTS = _env_list("PUSHIT_ALLOWED_HOSTS", "pushit-api.foxugly.com")
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

    from config.sentry import init_options

    # No PII, no local variables, no request body, and a `before_send` that
    # drops tokens, cookies and command output (I4, OPERATIONS.md §3.25).
    sentry_sdk.init(
        **init_options(
            dsn=SENTRY_DSN,
            environment=_env("SENTRY_ENVIRONMENT", STATE),
            release=_env("SENTRY_RELEASE") or None,
            traces_sample_rate=float(_env("SENTRY_TRACES_SAMPLE_RATE", "0")),
        )
    )
