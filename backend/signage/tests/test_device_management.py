from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from signage.models import (
    Device,
    DeviceAccessToken,
    DeviceCommand,
    DeviceCredential,
    DeviceManagementCredential,
    User,
)
from signage.services import disable_device, revoke_device_credentials


@pytest.fixture
def managed_device():
    owner = User.objects.create_user(
        "management-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="MANAGED-01", status=Device.Status.ACTIVE)
    credential, _ = DeviceCredential.issue(device)
    _, access_token = DeviceAccessToken.issue(credential)
    return owner, device, access_token


@pytest.mark.django_db
def test_management_channel_survives_playback_disablement(client, managed_device):
    owner, device, access_token = managed_device
    bootstrap = client.post(
        reverse("device-management-bootstrap"),
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    management_token = bootstrap.json()["management_token"]

    disable_device(device, owner)
    client.force_login(owner)
    request = client.post(reverse("device-admin-mode", args=[device.id]))
    client.logout()

    assert request.status_code == 302
    assert (
        client.get(
            reverse("device-sync"),
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        ).status_code
        == 401
    )
    delivery = client.get(
        reverse("device-management-commands"),
        HTTP_AUTHORIZATION=f"Bearer {management_token}",
    )
    command = delivery.json()["command"]
    assert delivery.status_code == 200
    assert command["kind"] == DeviceCommand.Kind.ADMIN_MODE

    acknowledgement = client.post(
        reverse("device-management-commands"),
        {"command_id": command["id"]},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {management_token}",
    )

    assert acknowledgement.status_code == 200
    assert acknowledgement.json()["acknowledged"] is True
    assert (
        client.get(
            reverse("device-management-commands"),
            HTTP_AUTHORIZATION=f"Bearer {management_token}",
        ).json()["command"]
        is None
    )


@pytest.mark.django_db
def test_management_token_cannot_acknowledge_another_device_command(
    client, managed_device
):
    owner, device, access_token = managed_device
    bootstrap = client.post(
        reverse("device-management-bootstrap"),
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    other = Device.objects.create(label="MANAGED-02", status=Device.Status.ACTIVE)
    command = DeviceCommand.objects.create(
        device=other,
        kind=DeviceCommand.Kind.ADMIN_MODE,
        requested_by=owner,
        expires_at=timezone.now() + timedelta(minutes=10),
        delivered_at=timezone.now(),
    )

    response = client.post(
        reverse("device-management-commands"),
        {"command_id": str(command.id)},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {bootstrap.json()['management_token']}",
    )

    assert response.status_code == 400
    command.refresh_from_db()
    assert command.acknowledged_at is None


@pytest.mark.django_db
def test_only_owner_can_request_admin_mode(client, managed_device):
    _, device, _ = managed_device
    marketing = User.objects.create_user(
        "management-marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(marketing)

    response = client.post(reverse("device-admin-mode", args=[device.id]))

    assert response.status_code == 403
    assert not DeviceCommand.objects.exists()


@pytest.mark.django_db
def test_bootstrap_rotates_management_credential(client, managed_device):
    _, device, access_token = managed_device

    first = client.post(
        reverse("device-management-bootstrap"),
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    ).json()["management_token"]
    second = client.post(
        reverse("device-management-bootstrap"),
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    ).json()["management_token"]

    assert first != second
    assert DeviceManagementCredential.objects.get(device=device).token_hash
    assert (
        client.get(
            reverse("device-management-commands"),
            HTTP_AUTHORIZATION=f"Bearer {first}",
        ).status_code
        == 401
    )
    assert (
        client.get(
            reverse("device-management-commands"),
            HTTP_AUTHORIZATION=f"Bearer {second}",
        ).status_code
        == 200
    )


@pytest.mark.django_db
def test_explicit_credential_revocation_revokes_management_channel(
    client, managed_device
):
    owner, device, access_token = managed_device
    management_token = client.post(
        reverse("device-management-bootstrap"),
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    ).json()["management_token"]

    revoke_device_credentials(device, owner)

    assert (
        client.get(
            reverse("device-management-commands"),
            HTTP_AUTHORIZATION=f"Bearer {management_token}",
        ).status_code
        == 401
    )
