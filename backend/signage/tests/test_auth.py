import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from signage.models import Alert, Driver, User


@pytest.mark.django_db
def test_user_must_use_company_domain():
    with pytest.raises(ValidationError):
        User.objects.create_user("person@example.com", "A-very-long-password-123")


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
def test_marketing_cannot_manage_dashboard_users(client):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(user)

    response = client.get(reverse("user-list"))

    assert response.status_code == 403
