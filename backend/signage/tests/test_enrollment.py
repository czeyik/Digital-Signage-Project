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
    token_hash,
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


@pytest.mark.django_db
def test_dashboard_can_provision_device_with_assignment(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    client.force_login(owner)

    response = client.post(
        reverse("device-create"),
        {
            "device_label": "PILOT-03",
            "driver_internal_id": "D003",
            "driver_name": "Example Driver",
            "vehicle_registration": "WXY9012",
        },
    )

    assert response.status_code == 302
    device = Device.objects.get(label="PILOT-03")
    assignment = device.assignments.get(unassigned_at__isnull=True)
    assert assignment.driver.internal_id == "D003"
    assert assignment.vehicle.registration == "WXY9012"


@pytest.mark.django_db
def test_marketing_cannot_open_driver_name_device_provisioning(client):
    user = User.objects.create_user(
        "marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(user)

    response = client.get(reverse("device-create"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_reassignment_preserves_assignment_history(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="PILOT-04")
    driver = Driver.objects.create(internal_id="D004", name="Old Driver")
    vehicle = Vehicle.objects.create(registration="OLD1234")
    old_assignment = DeviceAssignment.objects.create(
        device=device, driver=driver, vehicle=vehicle
    )
    client.force_login(owner)

    response = client.post(
        reverse("device-reassign", args=[device.id]),
        {
            "driver_internal_id": "D005",
            "driver_name": "New Driver",
            "vehicle_registration": "NEW1234",
        },
    )

    assert response.status_code == 302
    old_assignment.refresh_from_db()
    assert old_assignment.unassigned_at is not None
    active_assignment = device.assignments.filter(unassigned_at__isnull=True).get()
    assert active_assignment.driver.internal_id == "D005"


@pytest.mark.django_db
def test_owner_pin_reset_shows_once_and_stores_only_hash(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="PILOT-05")
    client.force_login(owner)

    response = client.post(reverse("device-pin-reset", args=[device.id]))

    assert response.status_code == 302
    device.refresh_from_db()
    page = client.get(reverse("kiosk-pin"))
    assert page.status_code == 200
    pin = page.context["pin"]
    assert len(pin) == 6
    assert device.kiosk_pin_hash == token_hash(pin)
    assert pin not in device.kiosk_pin_hash
    assert client.get(reverse("kiosk-pin")).status_code == 302
