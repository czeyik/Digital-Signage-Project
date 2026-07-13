import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-unsafe-secret")
DEBUG = env_bool("DJANGO_DEBUG", True)
DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "development")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "signage",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "signage.middleware.SessionIdleTimeoutMiddleware",
    "signage.middleware.ProductionSecurityHeadersMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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
WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("DATABASE_URL"):
    from urllib.parse import urlparse

    database = urlparse(os.environ["DATABASE_URL"])
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": database.path.removeprefix("/"),
            "USER": database.username,
            "PASSWORD": database.password,
            "HOST": database.hostname,
            "PORT": database.port or 5432,
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"sslmode": os.getenv("DB_SSLMODE", "prefer")},
        }
    }
elif os.getenv("DB_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "signage"),
            "USER": os.getenv("DB_USER", "signage"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.environ["DB_HOST"],
            "PORT": int(os.getenv("DB_PORT", "5432")),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"sslmode": os.getenv("DB_SSLMODE", "require")},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "signage.User"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
        )
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Kuala_Lumpur"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_IDLE_TIMEOUT_SECONDS = 30 * 60
PASSWORD_RESET_TIMEOUT = 15 * 60
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
if env_bool("DJANGO_TRUST_X_FORWARDED_PROTO", not DEBUG):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", False)
SECURE_HSTS_SECONDS = 0 if DEBUG else 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "signage.authentication.DeviceAccessTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "signage.api.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "30/hour", "user": "600/hour"},
}

DEVICE_ACCESS_TOKEN_TTL_SECONDS = 60 * 60
ENROLLMENT_CODE_TTL_SECONDS = 15 * 60
ENROLLMENT_CHALLENGE_TTL_SECONDS = 5 * 60
REQUIRED_APP_VERSION = os.getenv("REQUIRED_APP_VERSION", "0.1.0")
DEVICE_OVERHEAT_CELSIUS = float(os.getenv("DEVICE_OVERHEAT_CELSIUS", "45"))
DEVICE_MEDIA_CACHE_BYTES = int(os.getenv("DEVICE_MEDIA_CACHE_BYTES", str(10 * 1024**3)))
DEVICE_EVENT_QUEUE_BYTES = int(
    os.getenv("DEVICE_EVENT_QUEUE_BYTES", str(500 * 1024**2))
)
DEVICE_MIN_FREE_BYTES = int(os.getenv("DEVICE_MIN_FREE_BYTES", str(2 * 1024**3)))
PLAY_INTEGRITY_PROJECT_NUMBER = os.getenv("PLAY_INTEGRITY_PROJECT_NUMBER", "")
PLAY_INTEGRITY_PACKAGE_NAME = os.getenv(
    "PLAY_INTEGRITY_PACKAGE_NAME", "com.duducar.signage"
)
PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON = os.getenv(
    "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON", ""
)
PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS = int(
    os.getenv("PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS", "120")
)
MEDIA_MAX_IMAGE_BYTES = 10 * 1024 * 1024
MEDIA_MAX_VIDEO_BYTES = 50 * 1024 * 1024
PILOT_BACKUP_ROOT = os.getenv("PILOT_BACKUP_ROOT", str(BASE_DIR / "backups"))
PILOT_BACKUP_RETENTION_DAYS = int(os.getenv("PILOT_BACKUP_RETENTION_DAYS", "30"))
PILOT_BACKUP_S3_BUCKET = os.getenv("PILOT_BACKUP_S3_BUCKET", "")

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND")
if not EMAIL_BACKEND:
    EMAIL_BACKEND = (
        "django.core.mail.backends.smtp.EmailBackend"
        if os.getenv("EMAIL_HOST")
        else "django.core.mail.backends.console.EmailBackend"
    )
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@duducar.co")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "duducar-signage",
    }
}

if os.getenv("AWS_STORAGE_BUCKET_NAME"):
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        },
    }
    AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"]
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME")
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")
    AWS_QUERYSTRING_AUTH = True
    AWS_QUERYSTRING_EXPIRE = 900
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": (
                "time={asctime} level={levelname} logger={name} message={message}"
            ),
            "style": "{",
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
