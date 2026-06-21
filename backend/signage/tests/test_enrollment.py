import pytest
from django.urls import reverse

from signage.models import (
    Device,
    DeviceAssignment,
    DeviceCredential,
    Driver,
    EnrollmentCode,
    User,
    Vehicle,
)


@pytest.mark.django_db
def test_enrollment_code_is_single_use(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="PILOT-01")
    driver = Driver.objects.create(internal_id="D001", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="WXY1234")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    _, raw_code = EnrollmentCode.issue(device, owner)
    payload = {
        "code": raw_code,
        "android_id": "android-test-id",
        "android_version": "12",
        "app_version": "0.1.0",
        "integrity_compromised": False,
    }

    first = client.post(
        reverse("device-enroll"), payload, content_type="application/json"
    )
    second = client.post(
        reverse("device-enroll"), payload, content_type="application/json"
    )

    assert first.status_code == 201
    assert "refresh_token" in first.json()
    assert second.status_code == 403


@pytest.mark.django_db
def test_compromised_device_cannot_enroll(client):
    response = client.post(
        reverse("device-enroll"),
        {
            "code": "123456",
            "android_id": "bad-device",
            "android_version": "12",
            "app_version": "0.1.0",
            "integrity_compromised": True,
        },
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_reenrollment_revokes_previous_device_credential(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="PILOT-02")
    driver = Driver.objects.create(internal_id="D002", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="WXY5678")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    payload = {
        "android_id": "android-test-id-2",
        "android_version": "12",
        "app_version": "0.1.0",
        "integrity_compromised": False,
    }

    _, first_code = EnrollmentCode.issue(device, owner)
    first = client.post(
        reverse("device-enroll"),
        {**payload, "code": first_code},
        content_type="application/json",
    )
    _, second_code = EnrollmentCode.issue(device, owner)
    second = client.post(
        reverse("device-enroll"),
        {**payload, "code": second_code},
        content_type="application/json",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    credentials = DeviceCredential.objects.filter(device=device).order_by("created_at")
    assert credentials.count() == 2
    assert credentials.first().revoked_at is not None
    assert credentials.last().revoked_at is None
