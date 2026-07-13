import secrets
from datetime import date
from io import StringIO

import pytest
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import override_settings

from signage.models import HardwareQualification, User

TEST_SECRET_KEY = secrets.token_urlsafe(32)


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


@override_settings(
    DEBUG=False,
    SECRET_KEY=TEST_SECRET_KEY,
    DATABASES={"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "x"}},
    AWS_STORAGE_BUCKET_NAME="duducar-signage-production-media",
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
)
def test_production_readiness_passes_for_configured_environment(monkeypatch):
    monkeypatch.setattr(
        "signage.management.commands.check_deployment_readiness.shutil.which",
        lambda executable: f"/usr/bin/{executable}",
    )
    out = StringIO()

    call_command("check_deployment_readiness", environment="production", stdout=out)

    assert "production deployment readiness checks passed" in out.getvalue()
