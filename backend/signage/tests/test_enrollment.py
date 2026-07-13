from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions

from signage.models import (
    Device,
    DeviceAssignment,
    DeviceCredential,
    Driver,
    EnrollmentChallenge,
    EnrollmentCode,
    HardwareQualification,
    User,
    Vehicle,
)


def enrollment_fixture():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    qualification = HardwareQualification(
        model_name="Canary Tablet",
        firmware_version="pilot-build-1",
        android_version="12",
        tested_by=owner,
        test_date=timezone.localdate(),
        evidence_reference="restricted/hardware/canary-tablet",
        approved_for_pilot=True,
    )
    for field_name in HardwareQualification.REQUIRED_PASS_FIELDS:
        setattr(qualification, field_name, True)
    qualification.save()
    device = Device.objects.create(
        label="INTEGRITY-01", hardware_qualification=qualification
    )
    driver = Driver.objects.create(internal_id="DI01", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="INT1234")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    _, raw_code = EnrollmentCode.issue(device, owner)
    return device, raw_code


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_production_challenge_requires_approved_hardware(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="UNQUALIFIED-01")
    driver = Driver.objects.create(internal_id="UNQUAL", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="UNQ1234")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    _, raw_code = EnrollmentCode.issue(device, owner)

    response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "unqualified-device",
            "android_version": "12",
            "app_version": "0.1.0",
        },
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_production_enrollment_requires_verified_single_use_challenge(
    client, monkeypatch
):
    device, raw_code = enrollment_fixture()
    challenge_response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "integrity-device",
            "android_version": "12",
            "app_version": "0.1.0",
        },
        content_type="application/json",
    )
    assert challenge_response.status_code == 201
    challenge = EnrollmentChallenge.objects.get()
    monkeypatch.setattr(
        "signage.api.verify_integrity_token",
        lambda token, expected: {"verified": bool(token and expected)},
    )
    payload = {
        "challenge_id": str(challenge.id),
        "integrity_token": "signed-token",
    }

    first = client.post(
        reverse("device-enroll"), payload, content_type="application/json"
    )
    replay = client.post(
        reverse("device-enroll"), payload, content_type="application/json"
    )

    assert first.status_code == 201
    assert replay.status_code == 403
    device.refresh_from_db()
    assert device.status == Device.Status.ACTIVE


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_failed_integrity_does_not_consume_enrollment(client, monkeypatch):
    _, raw_code = enrollment_fixture()
    challenge_response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "integrity-device",
            "android_version": "12",
            "app_version": "0.1.0",
        },
        content_type="application/json",
    )
    challenge_id = challenge_response.json()["challenge_id"]

    def reject(*args):
        raise exceptions.AuthenticationFailed("Device integrity requirements failed.")

    monkeypatch.setattr("signage.api.verify_integrity_token", reject)
    response = client.post(
        reverse("device-enroll"),
        {"challenge_id": challenge_id, "integrity_token": "forged"},
        content_type="application/json",
    )

    assert response.status_code == 403
    assert EnrollmentCode.objects.get().used_at is None
    assert EnrollmentChallenge.objects.get().used_at is None


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_expired_integrity_challenge_is_rejected_without_consuming_code(client):
    _, raw_code = enrollment_fixture()
    challenge_response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "integrity-device",
            "android_version": "12",
            "app_version": "0.1.0",
        },
        content_type="application/json",
    )
    challenge = EnrollmentChallenge.objects.get(
        pk=challenge_response.json()["challenge_id"]
    )
    challenge.expires_at = timezone.now() - timedelta(seconds=1)
    challenge.save(update_fields=["expires_at"])

    response = client.post(
        reverse("device-enroll"),
        {"challenge_id": str(challenge.id), "integrity_token": "signed-token"},
        content_type="application/json",
    )

    assert response.status_code == 403
    assert EnrollmentCode.objects.get().used_at is None


@pytest.mark.django_db
@override_settings(DEPLOYMENT_ENV="production")
def test_production_rejects_legacy_self_reported_integrity(client):
    response = client.post(
        reverse("device-enroll"),
        {
            "code": "123456",
            "android_id": "legacy-device",
            "android_version": "12",
            "app_version": "0.1.0",
            "integrity_compromised": False,
        },
        content_type="application/json",
    )
    assert response.status_code == 400


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
    algorithm, iterations, salt_hex, expected_hex = device.kiosk_pin_hash.split("$")
    import hashlib

    actual = hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), bytes.fromhex(salt_hex), int(iterations)
    ).hex()
    assert algorithm == "pbkdf2_sha256"
    assert actual == expected_hex
    assert pin not in device.kiosk_pin_hash
    assert client.get(reverse("kiosk-pin")).status_code == 302
