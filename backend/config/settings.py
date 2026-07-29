import os
import stat
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_csv(name):
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def secret_env_or_file(
    value_name,
    *,
    file_name=None,
    fallback_value_name=None,
    default="",
):
    file_name = file_name or f"{value_name}_FILE"
    file_path = os.getenv(file_name, "").strip()
    direct_values = [
        value
        for value in (
            os.getenv(value_name),
            os.getenv(fallback_value_name) if fallback_value_name else None,
        )
        if value is not None
    ]
    if file_path and direct_values:
        raise ImproperlyConfigured(
            f"Configure either {value_name} or {file_name}, not both."
        )
    if len(direct_values) > 1:
        raise ImproperlyConfigured(
            f"Configure only one environment value for {value_name}."
        )
    if file_path:
        secret_path = Path(file_path)
        try:
            file_status = secret_path.stat()
        except OSError:
            raise ImproperlyConfigured(f"{file_name} cannot be read.") from None
        if secret_path.is_symlink() or not secret_path.is_file():
            raise ImproperlyConfigured(f"{file_name} must name a regular file.")
        if stat.S_IMODE(file_status.st_mode) & 0o077:
            raise ImproperlyConfigured(
                f"{file_name} must not be accessible by group or other users."
            )
        try:
            value = secret_path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError:
            raise ImproperlyConfigured(f"{file_name} cannot be read.") from None
        if not value:
            raise ImproperlyConfigured(f"{file_name} is empty.")
    else:
        value = direct_values[0] if direct_values else default
    if value.startswith("-----BEGIN") and "\\n" in value:
        value = value.replace("\\n", "\n")
    return value


def regional_s3_endpoint(region):
    return f"https://s3.{region}.amazonaws.com" if region else None


SECRET_KEY = secret_env_or_file(
    "DJANGO_SECRET_KEY",
    default="development-only-unsafe-secret",
)
DEBUG = env_bool("DJANGO_DEBUG", True)
DEPLOYMENT_ENV = os.getenv("DEPLOYMENT_ENV", "development")
DEPLOYMENT_COMPONENT = os.getenv("DEPLOYMENT_COMPONENT", "all")
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
    "signage.middleware.HealthCheckMiddleware",
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
    DB_PASSWORD = secret_env_or_file("DB_PASSWORD")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "signage"),
            "USER": os.getenv("DB_USER", "signage"),
            "PASSWORD": DB_PASSWORD,
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
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

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
PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON = secret_env_or_file(
    "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON"
)
PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS = int(
    os.getenv("PLAY_INTEGRITY_MAX_TOKEN_AGE_SECONDS", "120")
)
MEDIA_MAX_IMAGE_BYTES = 10 * 1024 * 1024
MEDIA_MAX_VIDEO_BYTES = 50 * 1024 * 1024
MEDIA_PROCESSING_DISPATCH_BACKEND = os.getenv(
    "MEDIA_PROCESSING_DISPATCH_BACKEND", "disabled"
).lower()
MEDIA_PROCESSING_LEASE_SECONDS = int(
    os.getenv("MEDIA_PROCESSING_LEASE_SECONDS", "1800")
)
MEDIA_DISPATCH_RETRY_SECONDS = int(os.getenv("MEDIA_DISPATCH_RETRY_SECONDS", "600"))
MEDIA_MAX_DISPATCH_ATTEMPTS = int(os.getenv("MEDIA_MAX_DISPATCH_ATTEMPTS", "5"))
MEDIA_RECONCILE_MAX_ASSETS = int(os.getenv("MEDIA_RECONCILE_MAX_ASSETS", "25"))
# The USD 30 topology retains ECS only for isolated, on-demand Fargate media
# tasks; it does not run a continuous worker service.
ECS_MEDIA_REGION = os.getenv(
    "ECS_MEDIA_REGION", os.getenv("AWS_S3_REGION_NAME", "")
)
ECS_MEDIA_CLUSTER = os.getenv("ECS_MEDIA_CLUSTER", "")
ECS_MEDIA_TASK_DEFINITION = os.getenv("ECS_MEDIA_TASK_DEFINITION", "")
ECS_MEDIA_CONTAINER_NAME = os.getenv("ECS_MEDIA_CONTAINER_NAME", "application")
ECS_MEDIA_SUBNET_IDS = env_csv("ECS_MEDIA_SUBNET_IDS")
ECS_MEDIA_SECURITY_GROUP_IDS = env_csv("ECS_MEDIA_SECURITY_GROUP_IDS")
ECS_MEDIA_ASSIGN_PUBLIC_IP = env_bool("ECS_MEDIA_ASSIGN_PUBLIC_IP", False)
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
EMAIL_HOST_USER = secret_env_or_file("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = secret_env_or_file("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "duducar-signage",
    }
}

AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN", "").strip()
AWS_CLOUDFRONT_KEY_ID = os.getenv("AWS_CLOUDFRONT_KEY_ID", "").strip()
AWS_CLOUDFRONT_KEY = secret_env_or_file(
    "AWS_CLOUDFRONT_PRIVATE_KEY",
    file_name="AWS_CLOUDFRONT_PRIVATE_KEY_FILE",
    fallback_value_name="AWS_CLOUDFRONT_KEY",
)

if os.getenv("AWS_STORAGE_BUCKET_NAME"):
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
    AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"]
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME")
    AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL") or regional_s3_endpoint(
        AWS_S3_REGION_NAME
    )
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
