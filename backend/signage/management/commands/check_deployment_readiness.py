import json
import re
import shutil

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from signage.integrity import (
    configured_certificate_fingerprints,
    validate_play_integrity_credentials,
)


class Command(BaseCommand):
    help = "Validate environment isolation and production safety settings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--environment",
            choices=["development", "production"],
            default=settings.DEPLOYMENT_ENV,
        )
        parser.add_argument(
            "--component",
            choices=["all", "web", "media-worker", "scheduled"],
            default=settings.DEPLOYMENT_COMPONENT,
            help=(
                "Validate all components or one runtime role. The media-worker "
                "role is the isolated, one-off Fargate task image."
            ),
        )

    def handle(self, *args, **options):
        environment = options["environment"]
        component = options["component"]
        errors = []
        warnings = []
        if environment != settings.DEPLOYMENT_ENV:
            errors.append(
                "--environment must match runtime DEPLOYMENT_ENV "
                f"({settings.DEPLOYMENT_ENV})."
            )
        if environment == "production":
            if component in {"all", "media-worker"}:
                self._check_media_dependencies(errors)
            if component in {"all", "scheduled"}:
                self._check_database_tools(errors)
            self._check_production_settings(errors, warnings, component)
        else:
            if component in {"all", "media-worker"}:
                self._check_media_dependencies(warnings)
            self._check_development_settings(warnings)
        if warnings:
            for warning in warnings:
                self.stdout.write(self.style.WARNING(warning))
        if errors:
            raise CommandError("Deployment readiness failed:\n- " + "\n- ".join(errors))
        self.stdout.write(
            self.style.SUCCESS(f"{environment} deployment readiness checks passed.")
        )

    def _check_media_dependencies(self, errors):
        for executable in ("ffmpeg", "ffprobe", "clamscan"):
            if not shutil.which(executable):
                errors.append(f"{executable} is required for media processing.")

    def _check_database_tools(self, errors):
        for executable in ("pg_dump", "pg_restore"):
            if not shutil.which(executable):
                errors.append(f"{executable} is required for backup and restore.")

    def _check_production_settings(self, errors, warnings, component):
        if settings.DEPLOYMENT_ENV != "production":
            errors.append("DEPLOYMENT_ENV must be production for a production check.")
        self._check_app_update_configuration(errors)
        if settings.DEBUG:
            errors.append("DJANGO_DEBUG must be false in production.")
        if (
            settings.SECRET_KEY.startswith("development-only")
            or "change-me" in settings.SECRET_KEY
        ):
            errors.append("DJANGO_SECRET_KEY must be a production secret.")
        database_engine = settings.DATABASES["default"]["ENGINE"]
        if "postgresql" not in database_engine:
            errors.append("Production must use PostgreSQL, not SQLite.")
        if not settings.DATABASES["default"].get("PASSWORD"):
            errors.append("Production PostgreSQL credentials must include a password.")
        if not getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""):
            errors.append("Production media storage must use a private object bucket.")
        if component in {"all", "scheduled"} and not settings.PILOT_BACKUP_S3_BUCKET:
            errors.append("PILOT_BACKUP_S3_BUCKET is required for database backups.")
        if component in {"all", "scheduled"}:
            self._check_backup_limits(errors)
        if component in {"all", "web", "media-worker"}:
            self._check_media_processing_limits(errors)
        if component in {"all", "web"}:
            self._check_web_settings(errors)

    def _check_app_update_configuration(self, errors):
        version_code = settings.APP_UPDATE_VERSION_CODE
        if not 0 <= version_code <= 2_147_483_647:
            errors.append(
                "APP_UPDATE_VERSION_CODE must be zero or a positive 32-bit integer."
            )
            return
        if version_code == 0:
            if any(
                (
                    settings.APP_UPDATE_VERSION_NAME,
                    settings.APP_UPDATE_STORAGE_NAME,
                    settings.APP_UPDATE_SHA256,
                    settings.APP_UPDATE_SIZE_BYTES,
                    settings.APP_UPDATE_ROLLOUT_PERCENT,
                )
            ):
                errors.append(
                    "Disabled app-update configuration must leave all staged APK "
                    "fields empty or zero."
                )
            return
        if not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?",
            settings.APP_UPDATE_VERSION_NAME,
        ):
            errors.append(
                "APP_UPDATE_VERSION_NAME must be an explicit semantic version."
            )
        if not re.fullmatch(
            r"updates/[A-Za-z0-9._/-]+\.apk", settings.APP_UPDATE_STORAGE_NAME
        ):
            errors.append(
                "APP_UPDATE_STORAGE_NAME must be an updates/*.apk object key."
            )
        if not re.fullmatch(r"[0-9a-f]{64}", settings.APP_UPDATE_SHA256):
            errors.append("APP_UPDATE_SHA256 must be a lowercase 64-hex digest.")
        if not 1 <= settings.APP_UPDATE_SIZE_BYTES <= 200 * 1024 * 1024:
            errors.append("APP_UPDATE_SIZE_BYTES must be between 1 and 200 MiB.")
        if not 1 <= settings.APP_UPDATE_ROLLOUT_PERCENT <= 100:
            errors.append("APP_UPDATE_ROLLOUT_PERCENT must be between 1 and 100.")

    def _check_backup_limits(self, errors):
        backup_limits = {
            "PILOT_BACKUP_RETENTION_DAYS": settings.PILOT_BACKUP_RETENTION_DAYS,
            "PILOT_BACKUP_MAX_LOCAL_ARCHIVES": (
                settings.PILOT_BACKUP_MAX_LOCAL_ARCHIVES
            ),
        }
        for name, value in backup_limits.items():
            if not 1 <= value <= 30:
                errors.append(f"{name} must be between 1 and 30.")

    def _check_media_processing_limits(self, errors):
        positive_settings = {
            "MEDIA_MAX_IMAGE_PIXELS": settings.MEDIA_MAX_IMAGE_PIXELS,
            "MEDIA_MAX_IMAGE_DIMENSION": settings.MEDIA_MAX_IMAGE_DIMENSION,
            "MEDIA_CLAMAV_TIMEOUT_SECONDS": settings.MEDIA_CLAMAV_TIMEOUT_SECONDS,
            "MEDIA_FFPROBE_TIMEOUT_SECONDS": settings.MEDIA_FFPROBE_TIMEOUT_SECONDS,
            "MEDIA_FFMPEG_TIMEOUT_SECONDS": settings.MEDIA_FFMPEG_TIMEOUT_SECONDS,
            "FRESHCLAM_TIMEOUT_SECONDS": settings.FRESHCLAM_TIMEOUT_SECONDS,
            "MEDIA_WORKER_TIMEOUT_SECONDS": settings.MEDIA_WORKER_TIMEOUT_SECONDS,
        }
        for name, value in positive_settings.items():
            if value < 1:
                errors.append(f"{name} must be positive.")
        minimum_request_bytes = (
            settings.MEDIA_MAX_VIDEO_BYTES
            + settings.MEDIA_UPLOAD_MULTIPART_ALLOWANCE_BYTES
        )
        if settings.MEDIA_MAX_REQUEST_BYTES < minimum_request_bytes:
            errors.append(
                "MEDIA_MAX_REQUEST_BYTES must allow the 50 MiB video limit plus "
                "1 MiB of multipart overhead."
            )
        if settings.MEDIA_MAX_IMAGE_PIXELS > 25_000_000:
            errors.append("MEDIA_MAX_IMAGE_PIXELS must not exceed 25000000.")
        if settings.MEDIA_MAX_IMAGE_DIMENSION > 10_000:
            errors.append("MEDIA_MAX_IMAGE_DIMENSION must not exceed 10000.")
        if (
            settings.MEDIA_WORKER_TIMEOUT_SECONDS
            >= settings.MEDIA_PROCESSING_LEASE_SECONDS
        ):
            errors.append(
                "MEDIA_WORKER_TIMEOUT_SECONDS must be shorter than "
                "MEDIA_PROCESSING_LEASE_SECONDS."
            )

    def _check_web_settings(self, errors):
        self._check_playback_batch_limits(errors)
        required_hosts = {
            "marketing.duducaradmin.com",
            "api.marketing.duducaradmin.com",
        }
        missing_hosts = required_hosts.difference(settings.ALLOWED_HOSTS)
        if missing_hosts:
            errors.append(
                "DJANGO_ALLOWED_HOSTS is missing: " + ", ".join(sorted(missing_hosts))
            )
        if "https://marketing.duducaradmin.com" not in settings.CSRF_TRUSTED_ORIGINS:
            errors.append("CSRF trusted origins must include the dashboard origin.")
        if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
            errors.append("Secure cookies must be enabled in production.")
        if not settings.SECURE_SSL_REDIRECT:
            errors.append("SECURE_SSL_REDIRECT must be enabled in production.")
        if not getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
            errors.append(
                "SECURE_PROXY_SSL_HEADER must trust the TLS proxy in production."
            )
        if settings.EMAIL_BACKEND.endswith("console.EmailBackend"):
            errors.append("Production email backend cannot be console-only.")
        if settings.EMAIL_BACKEND.endswith("smtp.EmailBackend"):
            if not settings.EMAIL_HOST:
                errors.append("EMAIL_HOST must be set for production SMTP email.")
            if not settings.DEFAULT_FROM_EMAIL:
                errors.append("DEFAULT_FROM_EMAIL must be set for production email.")
        if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL:
            errors.append("Email cannot enable both TLS and SSL.")
        if not settings.PLAY_INTEGRITY_PROJECT_NUMBER:
            errors.append("PLAY_INTEGRITY_PROJECT_NUMBER is required in production.")
        if not settings.PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON:
            errors.append(
                "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON is required in production."
            )
        else:
            try:
                credentials = json.loads(settings.PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON)
            except json.JSONDecodeError:
                errors.append("PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON is invalid JSON.")
            else:
                try:
                    validate_play_integrity_credentials(credentials)
                except ImproperlyConfigured as exc:
                    errors.append(str(exc))
        if settings.PLAY_INTEGRITY_PACKAGE_NAME != "com.duducar.signage":
            errors.append("PLAY_INTEGRITY_PACKAGE_NAME must match the Android package.")
        try:
            configured_certificate_fingerprints()
        except ImproperlyConfigured as exc:
            errors.append(str(exc))
        if (
            "signage.middleware.ProductionSecurityHeadersMiddleware"
            not in settings.MIDDLEWARE
        ):
            errors.append("Production security headers middleware is required.")
        self._check_cloudfront_signing(errors)
        self._check_media_dispatch(errors)
        if not 1 <= settings.CSV_EXPORT_MAX_ROWS <= 10_000:
            errors.append("CSV_EXPORT_MAX_ROWS must be between 1 and 10000.")
        if not 1 <= settings.AUTH_ARTIFACT_RETENTION_DAYS <= 90:
            errors.append("AUTH_ARTIFACT_RETENTION_DAYS must be between 1 and 90.")

    def _check_playback_batch_limits(self, errors):
        compressed = settings.PLAYBACK_BATCH_MAX_COMPRESSED_BYTES
        decompressed = settings.PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES
        if not 1 <= compressed <= 256 * 1024:
            errors.append(
                "PLAYBACK_BATCH_MAX_COMPRESSED_BYTES must be between 1 and 262144."
            )
        if not 1 <= decompressed <= 1024 * 1024:
            errors.append(
                "PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES must be between 1 and 1048576."
            )
        if compressed > decompressed:
            errors.append(
                "PLAYBACK_BATCH_MAX_COMPRESSED_BYTES must not exceed the "
                "decompressed limit."
            )

    def _check_cloudfront_signing(self, errors):
        domain = getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "")
        key_id = getattr(settings, "AWS_CLOUDFRONT_KEY_ID", "")
        private_key = getattr(settings, "AWS_CLOUDFRONT_KEY", "")
        if not domain:
            errors.append(
                "AWS_S3_CUSTOM_DOMAIN must use the private CloudFront distribution."
            )
        elif "://" in domain or "/" in domain:
            errors.append("AWS_S3_CUSTOM_DOMAIN must be a hostname without a scheme.")
        if not key_id or not private_key:
            errors.append(
                "CloudFront key ID and private key are required for signed media URLs."
            )
        if not getattr(settings, "AWS_QUERYSTRING_AUTH", False):
            errors.append("Signed media URLs must keep AWS_QUERYSTRING_AUTH enabled.")
        query_expiry = getattr(settings, "AWS_QUERYSTRING_EXPIRE", 0)
        if not 1 <= query_expiry <= 900:
            errors.append("Signed media URLs must expire within 900 seconds.")
        if private_key:
            try:
                from cryptography.hazmat.primitives.asymmetric.rsa import (
                    RSAPrivateKey,
                )
                from cryptography.hazmat.primitives.serialization import (
                    load_pem_private_key,
                )

                signing_key = load_pem_private_key(
                    private_key.encode("utf-8"),
                    password=None,
                )
                if not isinstance(signing_key, RSAPrivateKey):
                    raise ValueError
            except (ImportError, TypeError, ValueError):
                errors.append("CloudFront signing private key must be a valid RSA PEM.")

    def _check_media_dispatch(self, errors):
        if settings.MEDIA_PROCESSING_DISPATCH_BACKEND != "ecs":
            errors.append(
                "Production web media dispatch must use one-off ECS/Fargate tasks."
            )
        required = {
            "ECS_MEDIA_REGION": settings.ECS_MEDIA_REGION,
            "ECS_MEDIA_CLUSTER": settings.ECS_MEDIA_CLUSTER,
            "ECS_MEDIA_TASK_DEFINITION": settings.ECS_MEDIA_TASK_DEFINITION,
            "ECS_MEDIA_CONTAINER_NAME": settings.ECS_MEDIA_CONTAINER_NAME,
            "ECS_MEDIA_SUBNET_IDS": settings.ECS_MEDIA_SUBNET_IDS,
            "ECS_MEDIA_SECURITY_GROUP_IDS": settings.ECS_MEDIA_SECURITY_GROUP_IDS,
        }
        for name, value in required.items():
            if not value:
                errors.append(f"{name} is required for one-off media processing.")
        positive_settings = {
            "MEDIA_PROCESSING_LEASE_SECONDS": settings.MEDIA_PROCESSING_LEASE_SECONDS,
            "MEDIA_DISPATCH_RETRY_SECONDS": settings.MEDIA_DISPATCH_RETRY_SECONDS,
            "MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS": (
                settings.MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS
            ),
            "MEDIA_MAX_DISPATCH_ATTEMPTS": settings.MEDIA_MAX_DISPATCH_ATTEMPTS,
            "MEDIA_RECONCILE_MAX_ASSETS": settings.MEDIA_RECONCILE_MAX_ASSETS,
            "MEDIA_DISPATCH_MAX_CONCURRENT_TASKS": (
                settings.MEDIA_DISPATCH_MAX_CONCURRENT_TASKS
            ),
            "MEDIA_DISPATCH_MAX_TASKS_PER_HOUR": (
                settings.MEDIA_DISPATCH_MAX_TASKS_PER_HOUR
            ),
            "MEDIA_DISPATCH_STARTUP_GRACE_SECONDS": (
                settings.MEDIA_DISPATCH_STARTUP_GRACE_SECONDS
            ),
            "MEDIA_DISPATCH_AWS_CONNECT_TIMEOUT_SECONDS": (
                settings.MEDIA_DISPATCH_AWS_CONNECT_TIMEOUT_SECONDS
            ),
            "MEDIA_DISPATCH_AWS_READ_TIMEOUT_SECONDS": (
                settings.MEDIA_DISPATCH_AWS_READ_TIMEOUT_SECONDS
            ),
        }
        for name, value in positive_settings.items():
            if value < 1:
                errors.append(f"{name} must be positive.")
        if settings.MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS > 3600:
            errors.append(
                "MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS must not exceed 3600."
            )
        if settings.MEDIA_DISPATCH_MAX_CONCURRENT_TASKS > 2:
            errors.append(
                "MEDIA_DISPATCH_MAX_CONCURRENT_TASKS must not exceed the "
                "pilot cap of 2."
            )
        if settings.MEDIA_DISPATCH_MAX_TASKS_PER_HOUR > 6:
            errors.append(
                "MEDIA_DISPATCH_MAX_TASKS_PER_HOUR must not exceed the "
                "pilot budget cap of 6."
            )

    def _check_development_settings(self, warnings):
        production_hosts = {
            "marketing.duducaradmin.com",
            "api.marketing.duducaradmin.com",
        }
        configured = production_hosts.intersection(settings.ALLOWED_HOSTS)
        if configured:
            warnings.append(
                "Development ALLOWED_HOSTS includes production hostnames: "
                + ", ".join(sorted(configured))
            )
