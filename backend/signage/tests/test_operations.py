import hashlib
import os
import secrets
import subprocess
from datetime import date
from io import StringIO

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import CommandError, call_command
from django.test import Client, override_settings
from storages.backends.s3 import S3Storage

from config import settings as config_settings
from config.settings import (
    regional_s3_endpoint,
    secret_env_or_file,
    staticfiles_storage_backend,
)
from signage.management.commands.check_deployment_readiness import (
    Command as ReadinessCommand,
)
from signage.management.commands.create_postgres_backup import BACKUP_PREFIX
from signage.management.commands.create_postgres_backup import (
    Command as PostgresBackupCommand,
)
from signage.models import HardwareQualification, User

TEST_SECRET_KEY = secrets.token_urlsafe(32)
TEST_CLOUDFRONT_PRIVATE_KEY = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    .decode("ascii")
)


@pytest.mark.django_db
def test_hardware_cannot_be_approved_until_required_tests_pass():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    qualification = HardwareQualification(
        model_name="Example 10",
        firmware_version="1.0",
        android_version="12",
        tested_by=owner,
        test_date=date.today(),
        evidence_reference="internal://hardware/example-10",
        approved_for_pilot=True,
    )

    with pytest.raises(ValidationError):
        qualification.save()


@pytest.mark.django_db
def test_hardware_approval_records_approved_timestamp():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    qualification = HardwareQualification(
        model_name="Example 10",
        firmware_version="1.0",
        android_version="12",
        tested_by=owner,
        test_date=date.today(),
        evidence_reference="internal://hardware/example-10",
        approved_for_pilot=True,
        **{field: True for field in HardwareQualification.REQUIRED_PASS_FIELDS},
    )

    qualification.save()

    assert qualification.approved_at is not None


def test_staticfiles_storage_matches_runtime_mode():
    assert (
        staticfiles_storage_backend(True)
        == "django.contrib.staticfiles.storage.StaticFilesStorage"
    )
    assert (
        staticfiles_storage_backend(False)
        == "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )
    assert (
        config_settings.STORAGES["staticfiles"]["BACKEND"]
        == staticfiles_storage_backend(config_settings.DEBUG)
    )


def test_s3_endpoint_uses_configured_region():
    assert regional_s3_endpoint("ap-southeast-5") == (
        "https://s3.ap-southeast-5.amazonaws.com"
    )


def test_secret_file_loader_requires_private_unambiguous_file(
    tmp_path,
    monkeypatch,
):
    secret_file = tmp_path / "application-secret"
    secret_file.write_text("multiline\nsecret\n", encoding="utf-8")
    secret_file.chmod(0o600)
    monkeypatch.delenv("TEST_APPLICATION_SECRET", raising=False)
    monkeypatch.setenv("TEST_APPLICATION_SECRET_FILE", str(secret_file))

    assert secret_env_or_file("TEST_APPLICATION_SECRET") == "multiline\nsecret"

    secret_file.chmod(0o644)
    with pytest.raises(ImproperlyConfigured, match="group or other"):
        secret_env_or_file("TEST_APPLICATION_SECRET")

    secret_file.chmod(0o600)
    monkeypatch.setenv("TEST_APPLICATION_SECRET", "ambiguous")
    with pytest.raises(ImproperlyConfigured, match="either"):
        secret_env_or_file("TEST_APPLICATION_SECRET")


@override_settings(
    AWS_STORAGE_BUCKET_NAME="private-media",
    AWS_S3_CUSTOM_DOMAIN="media.example.cloudfront.net",
    AWS_CLOUDFRONT_KEY_ID="KTEST",
    AWS_CLOUDFRONT_KEY=TEST_CLOUDFRONT_PRIVATE_KEY,
    AWS_QUERYSTRING_AUTH=True,
    AWS_QUERYSTRING_EXPIRE=900,
)
def test_s3_storage_builds_short_lived_cloudfront_signed_url():
    storage = S3Storage()

    url = storage.url("validated/poster.png")

    assert url.startswith(
        "https://media.example.cloudfront.net/validated/poster.png?"
    )
    assert "Key-Pair-Id=KTEST" in url
    assert "Signature=" in url
    assert "Expires=" in url


@pytest.mark.django_db
def test_pilot_backup_can_be_created_and_verified(tmp_path):
    User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )

    call_command(
        "create_pilot_backup",
        output_dir=str(tmp_path),
        skip_media=True,
        verbosity=0,
    )
    archive = next(tmp_path.glob("duducar-signage-*.tar.gz"))
    out = StringIO()
    call_command("verify_pilot_backup", str(archive), stdout=out)

    assert "Verified backup" in out.getvalue()


@override_settings(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "signage",
            "USER": "signage",
            "PASSWORD": "database-secret",
            "HOST": "127.0.0.1",
            "PORT": 5432,
            "OPTIONS": {"sslmode": "require"},
        }
    },
    PILOT_BACKUP_S3_BUCKET="backup-bucket",
)
def test_postgres_backup_is_custom_format_validated_hashed_and_uploaded(
    tmp_path,
    monkeypatch,
):
    process_calls = []
    uploads = []
    old_archive = tmp_path / "duducar-signage-postgres-20000101T000000Z.dump"
    old_digest = tmp_path / "duducar-signage-postgres-20000101T000000Z.dump.sha256"
    old_archive.write_bytes(b"expired backup")
    old_digest.write_text("expired", encoding="ascii")
    os.utime(old_archive, (946684800, 946684800))
    os.utime(old_digest, (946684800, 946684800))

    def fake_which(executable):
        return f"/usr/bin/{executable}"

    def fake_run(command, **kwargs):
        process_calls.append((command, kwargs))
        if command[0].endswith("pg_dump"):
            destination = command[command.index("--file") + 1]
            with open(destination, "wb") as archive:
                archive.write(b"test custom-format archive")
            assert "database-secret" not in command
            assert kwargs["env"]["PGPASSWORD"] == "database-secret"
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    class S3Client:
        def upload_file(self, source, bucket, key, ExtraArgs=None):
            uploads.append((source, bucket, key, ExtraArgs))

    monkeypatch.setattr(
        "signage.management.commands.create_postgres_backup.shutil.which",
        fake_which,
    )
    monkeypatch.setattr(
        "signage.management.commands.create_postgres_backup.subprocess.run",
        fake_run,
    )
    monkeypatch.setattr(
        "signage.management.commands.create_postgres_backup.boto3.client",
        lambda service: S3Client(),
    )
    out = StringIO()

    call_command(
        "create_postgres_backup",
        output_dir=str(tmp_path),
        stdout=out,
    )

    archive = next(tmp_path.glob("*.dump"))
    digest_file = next(tmp_path.glob("*.dump.sha256"))
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert "--format=custom" in process_calls[0][0]
    assert process_calls[1][0][1] == "--list"
    assert digest_file.read_text(encoding="ascii").startswith(expected)
    assert len(uploads) == 2
    assert all(upload[2].startswith("database-backups/") for upload in uploads)
    assert uploads[0][3]["Metadata"]["sha256"] == expected
    assert "database-secret" not in out.getvalue()
    assert not old_archive.exists()
    assert not old_digest.exists()


def test_local_postgres_backup_cache_is_capped_by_archive_count(tmp_path):
    now = 1_900_000_000
    for number in range(4):
        archive = tmp_path / f"{BACKUP_PREFIX}-2026010{number + 1}T000000Z.dump"
        digest = archive.with_suffix(".dump.sha256")
        archive.write_bytes(f"archive-{number}".encode())
        digest.write_text(f"digest-{number}", encoding="ascii")
        os.utime(archive, (now + number, now + number))
        os.utime(digest, (now + number, now + number))

    PostgresBackupCommand()._prune_old_backups(
        tmp_path,
        retain_days=30_000,
        max_local_archives=2,
    )

    assert len(list(tmp_path.glob(f"{BACKUP_PREFIX}-*.dump"))) == 2
    assert len(list(tmp_path.glob(f"{BACKUP_PREFIX}-*.dump.sha256"))) == 2


@override_settings(MEDIA_MAX_REQUEST_BYTES=100)
def test_media_upload_request_size_is_rejected_before_authentication():
    client = Client()

    response = client.post(
        "/media/upload/",
        data=b"x",
        content_type="application/octet-stream",
        CONTENT_LENGTH="101",
    )

    assert response.status_code == 413
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Security-Policy"].startswith("default-src 'self'")


@override_settings(
    DEBUG=True,
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    AWS_STORAGE_BUCKET_NAME="",
    ALLOWED_HOSTS=["localhost"],
    CSRF_TRUSTED_ORIGINS=[],
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    SECURE_SSL_REDIRECT=False,
)
def test_production_readiness_fails_for_unsafe_environment():
    with pytest.raises(CommandError):
        call_command("check_deployment_readiness", environment="production")


@override_settings(MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS=3601)
def test_readiness_rejects_dispatch_reuse_beyond_ecs_safe_window():
    errors = []

    ReadinessCommand()._check_media_dispatch(errors)

    assert (
        "MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS must not exceed 3600." in errors
    )


@override_settings(
    MEDIA_MAX_IMAGE_PIXELS=0,
    MEDIA_MAX_IMAGE_DIMENSION=0,
    MEDIA_CLAMAV_TIMEOUT_SECONDS=0,
    MEDIA_FFPROBE_TIMEOUT_SECONDS=0,
    MEDIA_FFMPEG_TIMEOUT_SECONDS=0,
    FRESHCLAM_TIMEOUT_SECONDS=0,
    MEDIA_WORKER_TIMEOUT_SECONDS=0,
    MEDIA_DISPATCH_MAX_CONCURRENT_TASKS=0,
    MEDIA_DISPATCH_MAX_TASKS_PER_HOUR=0,
    MEDIA_DISPATCH_STARTUP_GRACE_SECONDS=0,
    MEDIA_DISPATCH_AWS_CONNECT_TIMEOUT_SECONDS=0,
    MEDIA_DISPATCH_AWS_READ_TIMEOUT_SECONDS=0,
    PLAYBACK_BATCH_MAX_COMPRESSED_BYTES=0,
    PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES=0,
    PILOT_BACKUP_RETENTION_DAYS=0,
    PILOT_BACKUP_MAX_LOCAL_ARCHIVES=0,
)
def test_readiness_rejects_nonpositive_resource_and_timeout_limits():
    errors = []
    command = ReadinessCommand()

    command._check_media_processing_limits(errors)
    command._check_media_dispatch(errors)
    command._check_playback_batch_limits(errors)
    command._check_backup_limits(errors)

    expected_names = {
        "MEDIA_MAX_IMAGE_PIXELS",
        "MEDIA_MAX_IMAGE_DIMENSION",
        "MEDIA_CLAMAV_TIMEOUT_SECONDS",
        "MEDIA_FFPROBE_TIMEOUT_SECONDS",
        "MEDIA_FFMPEG_TIMEOUT_SECONDS",
        "FRESHCLAM_TIMEOUT_SECONDS",
        "MEDIA_WORKER_TIMEOUT_SECONDS",
        "MEDIA_DISPATCH_MAX_CONCURRENT_TASKS",
        "MEDIA_DISPATCH_MAX_TASKS_PER_HOUR",
        "MEDIA_DISPATCH_STARTUP_GRACE_SECONDS",
        "MEDIA_DISPATCH_AWS_CONNECT_TIMEOUT_SECONDS",
        "MEDIA_DISPATCH_AWS_READ_TIMEOUT_SECONDS",
        "PLAYBACK_BATCH_MAX_COMPRESSED_BYTES",
        "PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES",
        "PILOT_BACKUP_RETENTION_DAYS",
        "PILOT_BACKUP_MAX_LOCAL_ARCHIVES",
    }
    for name in expected_names:
        assert any(name in error for error in errors)


@override_settings(
    MEDIA_MAX_REQUEST_BYTES=50 * 1024 * 1024,
    MEDIA_MAX_IMAGE_PIXELS=25_000_001,
    MEDIA_MAX_IMAGE_DIMENSION=10_001,
    MEDIA_WORKER_TIMEOUT_SECONDS=1800,
    MEDIA_PROCESSING_LEASE_SECONDS=1800,
    MEDIA_DISPATCH_MAX_CONCURRENT_TASKS=3,
    MEDIA_DISPATCH_MAX_TASKS_PER_HOUR=7,
    PLAYBACK_BATCH_MAX_COMPRESSED_BYTES=256 * 1024 + 1,
    PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES=1024 * 1024 + 1,
    PILOT_BACKUP_RETENTION_DAYS=31,
    PILOT_BACKUP_MAX_LOCAL_ARCHIVES=31,
)
def test_readiness_rejects_limits_outside_pilot_safety_ranges():
    errors = []
    command = ReadinessCommand()

    command._check_media_processing_limits(errors)
    command._check_media_dispatch(errors)
    command._check_playback_batch_limits(errors)
    command._check_backup_limits(errors)

    expected_fragments = {
        "50 MiB video limit plus 1 MiB",
        "MEDIA_MAX_IMAGE_PIXELS must not exceed",
        "MEDIA_MAX_IMAGE_DIMENSION must not exceed",
        "MEDIA_WORKER_TIMEOUT_SECONDS must be shorter",
        "pilot cap of 2",
        "pilot budget cap of 6",
        "PLAYBACK_BATCH_MAX_COMPRESSED_BYTES must be between",
        "PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES must be between",
        "PILOT_BACKUP_RETENTION_DAYS must be between 1 and 30",
        "PILOT_BACKUP_MAX_LOCAL_ARCHIVES must be between 1 and 30",
    }
    for fragment in expected_fragments:
        assert any(fragment in error for error in errors)


@override_settings(
    DEBUG=False,
    SECRET_KEY=TEST_SECRET_KEY,
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "x",
            "PASSWORD": "test-password",
        }
    },
    AWS_STORAGE_BUCKET_NAME="duducar-signage-production-media",
    AWS_S3_CUSTOM_DOMAIN="media.example.cloudfront.net",
    AWS_CLOUDFRONT_KEY_ID="KTEST",
    AWS_CLOUDFRONT_KEY=TEST_CLOUDFRONT_PRIVATE_KEY,
    AWS_QUERYSTRING_AUTH=True,
    AWS_QUERYSTRING_EXPIRE=900,
    PILOT_BACKUP_S3_BUCKET="duducar-signage-production-backups",
    ALLOWED_HOSTS=["marketing.duducaradmin.com", "api.marketing.duducaradmin.com"],
    CSRF_TRUSTED_ORIGINS=["https://marketing.duducaradmin.com"],
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
    EMAIL_HOST="email-smtp.ap-southeast-1.amazonaws.com",
    DEFAULT_FROM_EMAIL="no-reply@duducar.co",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
    PLAY_INTEGRITY_PACKAGE_NAME="com.duducar.signage",
    PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON=(
        '{"type":"service_account","project_id":"duducar-signage-production",'
        '"private_key":"test-only-key","client_email":"integrity@example.test"}'
    ),
    MEDIA_PROCESSING_DISPATCH_BACKEND="ecs",
    ECS_MEDIA_REGION="ap-southeast-5",
    ECS_MEDIA_CLUSTER="production",
    ECS_MEDIA_TASK_DEFINITION="production-worker:1",
    ECS_MEDIA_CONTAINER_NAME="application",
    ECS_MEDIA_SUBNET_IDS=["subnet-a"],
    ECS_MEDIA_SECURITY_GROUP_IDS=["sg-worker"],
)
def test_production_readiness_passes_for_configured_environment(monkeypatch):
    monkeypatch.setattr(
        "signage.management.commands.check_deployment_readiness.shutil.which",
        lambda executable: f"/usr/bin/{executable}",
    )
    out = StringIO()

    call_command("check_deployment_readiness", environment="production", stdout=out)

    assert "production deployment readiness checks passed" in out.getvalue()


@override_settings(
    DEBUG=False,
    SECRET_KEY=TEST_SECRET_KEY,
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "x",
            "PASSWORD": "test-password",
        }
    },
    AWS_STORAGE_BUCKET_NAME="duducar-signage-production-media",
)
def test_media_worker_readiness_does_not_require_web_only_secrets(monkeypatch):
    monkeypatch.setattr(
        "signage.management.commands.check_deployment_readiness.shutil.which",
        lambda executable: f"/usr/bin/{executable}",
    )
    out = StringIO()

    call_command(
        "check_deployment_readiness",
        environment="production",
        component="media-worker",
        stdout=out,
    )

    assert "production deployment readiness checks passed" in out.getvalue()


@pytest.mark.django_db
@override_settings(
    DEBUG=False,
    ALLOWED_HOSTS=["marketing.duducaradmin.com", "api.marketing.duducaradmin.com"],
    SECURE_SSL_REDIRECT=True,
)
def test_health_checks_bypass_host_validation_only_for_health_endpoints():
    client = Client()

    live = client.get("/health/live/", HTTP_HOST="10.40.0.19:8000")
    ready = client.get("/health/ready/", HTTP_HOST="10.40.0.19:8000")
    login = client.get("/login/", HTTP_HOST="10.40.0.19:8000")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert login.status_code == 400
