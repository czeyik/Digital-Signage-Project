import shutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Validate environment isolation and production safety settings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--environment",
            choices=["development", "production"],
            default=settings.DEPLOYMENT_ENV,
        )

    def handle(self, *args, **options):
        environment = options["environment"]
        errors = []
        warnings = []
        if environment == "production":
            self._check_media_dependencies(errors)
            self._check_production_settings(errors, warnings)
        else:
            self._check_media_dependencies(warnings)
            self._check_development_settings(warnings)
        if warnings:
            for warning in warnings:
                self.stdout.write(self.style.WARNING(warning))
        if errors:
            raise CommandError(
                "Deployment readiness failed:\n- " + "\n- ".join(errors)
            )
        self.stdout.write(
            self.style.SUCCESS(f"{environment} deployment readiness checks passed.")
        )

    def _check_media_dependencies(self, errors):
        for executable in ("ffmpeg", "ffprobe", "clamscan"):
            if not shutil.which(executable):
                errors.append(f"{executable} is required for media processing.")

    def _check_production_settings(self, errors, warnings):
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
        if not getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""):
            errors.append("Production media storage must use a private object bucket.")
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
