import csv
import importlib
from datetime import timedelta
from io import StringIO

import pytest
from django.apps import apps
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from signage.models import Alert, AuditEvent, Driver, LoginThrottle, User


@pytest.mark.django_db
def test_user_must_use_company_domain():
    with pytest.raises(ValidationError):
        User.objects.create_user("person@example.com", "A-very-long-password-123")


@pytest.mark.django_db
def test_marketing_admin_access_data_migration_is_audited():
    legacy_user = User.objects.create(
        email="legacy-marketing@duducar.co",
        role=User.Role.MARKETING,
        is_staff=True,
        is_superuser=True,
    )
    Session.objects.create(
        session_key="legacy-one-time-secret-session",
        session_data="legacy raw one-time secret",
        expire_date=timezone.now() + timedelta(minutes=30),
    )
    migration = importlib.import_module(
        "signage.migrations.0009_revoke_marketing_admin_access"
    )

    migration.revoke_marketing_admin_access(apps, None)

    legacy_user.refresh_from_db()
    assert legacy_user.is_staff is False
    assert legacy_user.is_superuser is False
    assert Session.objects.count() == 0
    event = AuditEvent.objects.filter(
        action="user.marketing_admin_access.revoked"
    ).latest("pk")
    assert event.metadata == {
        "account_count": 1,
        "dashboard_sessions_revoked": 1,
    }


@pytest.mark.django_db
def test_dashboard_requires_login(client):
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert reverse("login") in response.url


@pytest.mark.django_db
def test_dashboard_renders_for_marketing_user(client):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert b"Proof of play: last 7 days" in response.content
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_marketing_user_cannot_see_driver_name_in_csv(client):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(user)
    response = client.get(reverse("playback-csv"))
    assert response.status_code == 200
    assert "driver_internal_id" in response.content.decode()
    assert "driver_name" not in response.content.decode()


@pytest.mark.django_db
def test_playback_csv_is_header_only_when_no_events(client):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(user)

    response = client.get(reverse("playback-csv"))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert list(csv.reader(StringIO(response.content.decode()))) == [
        [
            "event_id",
            "device",
            "vehicle",
            "driver_internal_id",
            "playlist",
            "media",
            "started_at",
            "status",
            "duration_ms",
            "captured_offline",
            "report_state",
            "latest_correction_status",
            "failure_category",
            "evidence_notice",
        ]
    ]


@pytest.mark.django_db
def test_driver_string_never_discloses_owner_only_name():
    driver = Driver.objects.create(internal_id="D001", name="Private Name")
    assert str(driver) == "D001"


@pytest.mark.django_db
def test_login_lockout_is_shared_database_state(client):
    User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    for _ in range(5):
        response = client.post(
            reverse("login"),
            {
                "username": "marketing@duducar.co",
                "password": "wrong-password",
            },
        )
        assert response.status_code == 200

    blocked = client.post(
        reverse("login"),
        {
            "username": "marketing@duducar.co",
            "password": "A-very-long-password-123",
        },
    )
    assert blocked.status_code == 200
    assert b"Too many sign-in attempts" in blocked.content
    assert Alert.objects.filter(code="suspicious_login_lockout").exists()
    throttle = LoginThrottle.objects.get()
    assert len(throttle.key_hash) == 64


@pytest.mark.django_db
def test_admin_login_uses_secure_lockout_and_audit_path(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    login_url = reverse("admin:login")
    for _ in range(5):
        response = client.post(
            login_url,
            {"username": owner.email, "password": "wrong-password"},
        )
        assert response.status_code == 200

    blocked = client.post(
        login_url,
        {"username": owner.email, "password": "A-very-long-password-123"},
    )

    assert blocked.status_code == 200
    assert b"Too many sign-in attempts" in blocked.content
    assert AuditEvent.objects.filter(action="auth.login_failed").count() == 5


@pytest.mark.django_db
def test_admin_login_authenticates_and_audits_owner(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )

    response = client.post(
        reverse("admin:login"),
        {"username": owner.email, "password": "A-very-long-password-123"},
    )

    assert response.status_code == 302
    assert response.url == reverse("admin:index")
    assert AuditEvent.objects.filter(actor=owner, action="auth.login").exists()


@pytest.mark.django_db
def test_login_does_not_create_session_if_audit_fails(client, monkeypatch):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("signage.views.audit", fail_audit)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            reverse("login"),
            {"username": user.email, "password": "A-very-long-password-123"},
        )

    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_admin_is_owner_only_even_for_legacy_staff_marketing_user(client):
    marketing = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    # Existing production rows may still carry is_staff=True from an older
    # release, so the site-level owner check must independently protect admin.
    User.objects.filter(pk=marketing.pk).update(is_staff=True)
    marketing.refresh_from_db()
    client.force_login(marketing)

    index = client.get(reverse("admin:index"))
    login = client.get(reverse("admin:login"))

    assert index.status_code == 302
    assert login.status_code == 403


@pytest.mark.django_db
def test_admin_login_rejects_legacy_staff_marketing_credentials(client):
    marketing = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    User.objects.filter(pk=marketing.pk).update(is_staff=True)

    response = client.post(
        reverse("admin:login"),
        {"username": marketing.email, "password": "A-very-long-password-123"},
    )

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session
    assert b"Invalid email or password" in response.content
    assert AuditEvent.objects.filter(action="auth.login_failed").exists()


@pytest.mark.django_db
def test_admin_protected_models_are_read_only(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    client.force_login(owner)

    assert client.get(reverse("admin:index")).status_code == 200
    assert client.get(reverse("admin:signage_device_add")).status_code == 403


@pytest.mark.django_db
def test_owner_driver_name_access_is_audited_in_admin(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    driver = Driver.objects.create(internal_id="D-PRIVATE", name="Private Name")
    client.force_login(owner)

    response = client.get(reverse("admin:signage_driver_change", args=[driver.pk]))

    assert response.status_code == 200
    assert b"Private Name" in response.content
    assert AuditEvent.objects.filter(
        actor=owner,
        action="driver.personal_data.view",
        target_id=str(driver.pk),
    ).exists()


@pytest.mark.django_db
def test_admin_logout_is_audited(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    client.force_login(owner)

    response = client.post(reverse("admin:logout"))

    assert response.status_code == 302
    assert AuditEvent.objects.filter(actor=owner, action="auth.logout").exists()


@pytest.mark.django_db
def test_admin_password_change_is_audited(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    client.force_login(owner)

    response = client.post(
        reverse("admin:password_change"),
        {
            "old_password": "A-very-long-password-123",
            "new_password1": "Another-very-long-password-456",
            "new_password2": "Another-very-long-password-456",
        },
    )

    assert response.status_code == 302
    assert AuditEvent.objects.filter(
        actor=owner, action="auth.password_change"
    ).exists()


@pytest.mark.django_db
def test_owner_can_create_dashboard_user(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    client.force_login(owner)

    response = client.post(
        reverse("user-create"),
        {
            "email": "new-user@duducar.co",
            "role": User.Role.MARKETING,
            "is_active": "on",
            "password": "Another-long-password-123",
        },
    )

    assert response.status_code == 302
    assert User.objects.filter(email="new-user@duducar.co").exists()


@pytest.mark.django_db
def test_dashboard_user_change_rolls_back_if_audit_fails(client, monkeypatch):
    owner = User.objects.create_user(
        "owner-rollback@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    marketing = User.objects.create_user(
        "marketing-rollback@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(owner)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("signage.views.audit", fail_audit)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            reverse("user-edit", args=[marketing.pk]),
            {
                "email": marketing.email,
                "role": User.Role.MARKETING,
                "password": "",
            },
        )

    marketing.refresh_from_db()
    assert marketing.is_active is True
    assert marketing.check_password("A-very-long-password-123")


@pytest.mark.django_db
def test_dashboard_user_blank_password_edit_preserves_existing_password(client):
    owner = User.objects.create_user(
        "owner-password-edit@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    marketing = User.objects.create_user(
        "marketing-password-edit@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(owner)

    response = client.post(
        reverse("user-edit", args=[marketing.pk]),
        {
            "email": marketing.email,
            "role": User.Role.MARKETING,
            "is_active": "on",
            "password": "",
        },
    )

    assert response.status_code == 302
    marketing.refresh_from_db()
    assert marketing.check_password("A-very-long-password-123")


@pytest.mark.django_db
def test_marketing_cannot_manage_dashboard_users(client):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(user)

    response = client.get(reverse("user-list"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_logout_invalidates_dashboard_session_and_is_audited(client):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(user)

    response = client.post(reverse("logout"))
    protected = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert protected.status_code == 302
    assert reverse("login") in protected.url
    assert AuditEvent.objects.filter(actor=user, action="auth.logout").exists()


@pytest.mark.django_db
def test_health_endpoints_and_security_headers(client):
    live = client.get(reverse("health-live"))
    ready = client.get(reverse("health-ready"))

    assert live.status_code == 200
    assert ready.status_code == 200
    assert live["Content-Security-Policy"].startswith("default-src 'self'")
    assert "geolocation=()" in live["Permissions-Policy"]
