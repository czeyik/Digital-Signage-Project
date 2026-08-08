from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN
from django.db import close_old_connections, connection, connections
from django.test import Client
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from signage.models import AuditEvent, User
from signage.views import InvalidPasswordResetToken, _complete_password_reset

CURRENT_CREDENTIAL = "A-very-long-password-123"
UPDATED_CREDENTIAL = "Another-very-long-password-456"


def reset_url(user, token=None):
    return reverse(
        "password_reset_confirm",
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": token or default_token_generator.make_token(user),
        },
    )


def open_reset_form(client, user, token=None):
    response = client.get(reset_url(user, token))
    assert response.status_code == 302
    assert response.url.endswith("/set-password/")
    assert client.session[INTERNAL_RESET_SESSION_TOKEN]
    return response.url


def submit_reset(client, confirmation_url, password=UPDATED_CREDENTIAL):
    return client.post(
        confirmation_url,
        {"new_password1": password, "new_password2": password},
    )


@pytest.mark.django_db
def test_password_reset_token_expires_after_fifteen_minutes(client, monkeypatch):
    assert settings.PASSWORD_RESET_TIMEOUT == 15 * 60
    user = User.objects.create_user(
        "reset-expiry@duducar.co", CURRENT_CREDENTIAL, role=User.Role.MARKETING
    )
    issued_at = datetime(2026, 8, 1, 0, 0, 0)
    monkeypatch.setattr(default_token_generator, "_now", lambda: issued_at)
    token = default_token_generator.make_token(user)
    monkeypatch.setattr(
        default_token_generator,
        "_now",
        lambda: issued_at + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 1),
    )

    response = client.get(reset_url(user, token))

    assert response.status_code == 200
    assert b"invalid or has expired" in response.content
    assert INTERNAL_RESET_SESSION_TOKEN not in client.session


@pytest.mark.django_db
def test_password_reset_is_audited_and_token_is_single_use(client):
    user = User.objects.create_user(
        "reset-audit@duducar.co", CURRENT_CREDENTIAL, role=User.Role.MARKETING
    )
    token = default_token_generator.make_token(user)
    confirmation_url = open_reset_form(client, user, token)

    response = submit_reset(client, confirmation_url)

    assert response.status_code == 302
    assert response.url == reverse("password_reset_complete")
    user.refresh_from_db()
    assert user.check_password(UPDATED_CREDENTIAL)
    assert AuditEvent.objects.filter(
        actor__isnull=True,
        action="auth.password_reset",
        target_type="signage.user",
        target_id=str(user.pk),
    ).count() == 1
    assert INTERNAL_RESET_SESSION_TOKEN not in client.session

    reused = client.get(reset_url(user, token))
    assert reused.status_code == 200
    assert b"invalid or has expired" in reused.content


@pytest.mark.django_db
def test_password_reset_invalidates_all_existing_sessions(client):
    user = User.objects.create_user(
        "reset-sessions@duducar.co", CURRENT_CREDENTIAL, role=User.Role.MARKETING
    )
    existing_session = Client()
    existing_session.force_login(user)
    assert existing_session.get(reverse("dashboard")).status_code == 200
    confirmation_url = open_reset_form(client, user)

    assert submit_reset(client, confirmation_url).status_code == 302

    protected = existing_session.get(reverse("dashboard"))
    assert protected.status_code == 302
    assert reverse("login") in protected.url
    assert "_auth_user_id" not in existing_session.session


@pytest.mark.django_db
def test_password_reset_rolls_back_if_audit_fails(client, monkeypatch):
    user = User.objects.create_user(
        "reset-rollback@duducar.co", CURRENT_CREDENTIAL, role=User.Role.MARKETING
    )
    token = default_token_generator.make_token(user)
    confirmation_url = open_reset_form(client, user, token)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("signage.views.audit", fail_audit)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        submit_reset(client, confirmation_url)

    user.refresh_from_db()
    assert user.check_password(CURRENT_CREDENTIAL)
    assert default_token_generator.check_token(user, token)
    assert not AuditEvent.objects.filter(action="auth.password_reset").exists()


@pytest.mark.django_db
def test_admin_password_change_rolls_back_if_audit_fails(client, monkeypatch):
    owner = User.objects.create_user(
        "owner-password-rollback@duducar.co",
        CURRENT_CREDENTIAL,
        role=User.Role.OWNER,
    )
    client.force_login(owner)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("signage.views.audit", fail_audit)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            reverse("admin:password_change"),
            {
                "old_password": CURRENT_CREDENTIAL,
                "new_password1": UPDATED_CREDENTIAL,
                "new_password2": UPDATED_CREDENTIAL,
            },
        )

    owner.refresh_from_db()
    assert owner.check_password(CURRENT_CREDENTIAL)
    assert not AuditEvent.objects.filter(action="auth.password_change").exists()
    assert client.get(reverse("admin:index")).status_code == 200


@pytest.mark.django_db(transaction=True)
def test_postgres_reset_token_is_consumed_once_under_concurrency():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL-specific password reset locking test")
    user = User.objects.create_user(
        "reset-concurrent@duducar.co",
        CURRENT_CREDENTIAL,
        role=User.Role.MARKETING,
    )
    token = default_token_generator.make_token(user)
    start_together = Barrier(2)

    def reset(password):
        close_old_connections()
        try:
            start_together.wait(timeout=5)
            _complete_password_reset(
                user.pk, token, password, default_token_generator
            )
        except InvalidPasswordResetToken:
            return "rejected"
        finally:
            connections.close_all()
        return "reset"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                reset,
                [
                    "Concurrent-password-choice-123",
                    "Concurrent-password-choice-456",
                ],
            )
        )

    assert sorted(outcomes) == ["rejected", "reset"]
    assert AuditEvent.objects.filter(action="auth.password_reset").count() == 1
