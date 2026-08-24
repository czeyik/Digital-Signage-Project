from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions

from signage.models import (
    Device,
    DeviceAccessToken,
    DeviceAssignment,
    DeviceCredential,
    Driver,
    EnrollmentChallenge,
    EnrollmentCode,
    HardwareQualification,
    User,
    Vehicle,
    token_hash,
)
from signage.services import issue_kiosk_pin

HARDWARE_DETAILS = {
    "hardware_model": "Canary Tablet",
    "firmware_version": "pilot-build-1",
    "security_patch_level": "2026-08-05",
}


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
        security_patch_level=HARDWARE_DETAILS["security_patch_level"],
        tested_by=owner,
        test_date=timezone.localdate(),
        evidence_reference="restricted/hardware/canary-tablet",
        measured_display_diagonal_inches=Decimal("10.00"),
    )
    qualification.save()
    device = Device.objects.create(
        label="INTEGRITY-01", hardware_qualification=qualification
    )
    driver = Driver.objects.create(internal_id="DI01", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="INT1234")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    issue_kiosk_pin(device, owner)
    _, raw_code = EnrollmentCode.issue(device, owner)
    return device, raw_code


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_production_challenge_requires_enrollment_eligible_hardware(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="UNQUALIFIED-01")
    driver = Driver.objects.create(internal_id="UNQUAL", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="UNQ1234")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    issue_kiosk_pin(device, owner)
    _, raw_code = EnrollmentCode.issue(device, owner)

    response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "unqualified-device",
            "android_version": "12",
            "app_version": "0.1.0",
            **HARDWARE_DETAILS,
        },
        content_type="application/json",
    )

    assert response.status_code == 403

    qualification = HardwareQualification(
        model_name=HARDWARE_DETAILS["hardware_model"],
        firmware_version=HARDWARE_DETAILS["firmware_version"],
        android_version="12",
        security_patch_level=HARDWARE_DETAILS["security_patch_level"],
        tested_by=owner,
        test_date=timezone.localdate(),
        evidence_reference="restricted/hardware/attested-canary-tablet",
        measured_display_diagonal_inches=Decimal("10.00"),
    )
    qualification.save()
    device.hardware_qualification = qualification
    device.save(update_fields=["hardware_qualification", "updated_at"])

    eligible = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "attested-device",
            "android_version": "12",
            "app_version": "0.1.0",
            **HARDWARE_DETAILS,
        },
        content_type="application/json",
    )

    assert eligible.status_code == 201
    assert not qualification.approved_for_pilot


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_production_challenge_rejects_out_of_range_hardware_record(client):
    device, raw_code = enrollment_fixture()
    qualification = device.hardware_qualification
    qualification.measured_display_diagonal_inches = Decimal("12.01")
    qualification.save()

    response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "out-of-range-device",
            "android_version": "12",
            "app_version": "0.1.0",
            **HARDWARE_DETAILS,
        },
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_production_challenge_rejects_approved_device_hardware_mismatch(client):
    _, raw_code = enrollment_fixture()

    response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "mismatched-device",
            "android_version": "12",
            "app_version": "0.1.0",
            **{**HARDWARE_DETAILS, "firmware_version": "unexpected-build"},
        },
        content_type="application/json",
    )

    assert response.status_code == 403
    assert not EnrollmentChallenge.objects.exists()


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
            **HARDWARE_DETAILS,
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
def test_production_enrollment_rejects_the_development_code_only_flow(client):
    device, raw_code = enrollment_fixture()

    response = client.post(
        reverse("device-enroll"),
        {
            "code": raw_code,
            "android_id": "development-flow-attempt",
            "android_version": "12",
            "app_version": "0.1.0-development",
        },
        content_type="application/json",
    )

    assert response.status_code == 400
    device.refresh_from_db()
    assert device.status == Device.Status.PENDING
    assert EnrollmentCode.objects.get(device=device).used_at is None


@pytest.mark.django_db
@override_settings(
    DEPLOYMENT_ENV="production",
    PLAY_INTEGRITY_PROJECT_NUMBER="123456789",
)
def test_device_disabled_after_challenge_cannot_finish_enrollment(client, monkeypatch):
    device, raw_code = enrollment_fixture()
    challenge_response = client.post(
        reverse("device-enrollment-challenge"),
        {
            "code": raw_code,
            "android_id": "disabled-after-challenge",
            "android_version": "12",
            "app_version": "0.1.0",
            **HARDWARE_DETAILS,
        },
        content_type="application/json",
    )
    challenge = EnrollmentChallenge.objects.get(
        pk=challenge_response.json()["challenge_id"]
    )
    Device.objects.filter(pk=device.pk).update(status=Device.Status.DISABLED)
    monkeypatch.setattr(
        "signage.api.verify_integrity_token",
        lambda token, expected: {"verified": bool(token and expected)},
    )

    response = client.post(
        reverse("device-enroll"),
        {
            "challenge_id": str(challenge.id),
            "integrity_token": "signed-token",
        },
        content_type="application/json",
    )

    assert response.status_code == 403
    device.refresh_from_db()
    challenge.refresh_from_db()
    assert device.status == Device.Status.DISABLED
    assert challenge.used_at is None
    assert EnrollmentCode.objects.get(pk=challenge.enrollment_id).used_at is None


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
            **HARDWARE_DETAILS,
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
            **HARDWARE_DETAILS,
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
    issue_kiosk_pin(device, owner)
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
def test_issuing_a_new_enrollment_code_expires_the_previous_code():
    device, first_code = enrollment_fixture()
    first = EnrollmentCode.objects.get(code_hash=token_hash(first_code))

    second, _ = EnrollmentCode.issue(device, first.created_by)

    first.refresh_from_db()
    assert first.is_usable is False
    assert second.is_usable is True


@pytest.mark.django_db
def test_enrollment_code_retries_a_global_six_digit_collision(monkeypatch):
    device, _ = enrollment_fixture()
    owner = EnrollmentCode.objects.get(device=device).created_by
    other_device = Device.objects.create(label="COLLISION-OTHER")
    other_driver = Driver.objects.create(internal_id="COLLISION", name="Example")
    other_vehicle = Vehicle.objects.create(registration="COLLIDE1")
    DeviceAssignment.objects.create(
        device=other_device,
        driver=other_driver,
        vehicle=other_vehicle,
    )
    issue_kiosk_pin(other_device, owner)
    EnrollmentCode.objects.create(
        device=other_device,
        code_hash=token_hash("000001"),
        expires_at=timezone.now() + timedelta(minutes=15),
        created_by=owner,
    )
    values = iter([1, 2])
    monkeypatch.setattr(
        "signage.models.secrets.randbelow", lambda upper: next(values)
    )

    enrollment, raw_code = EnrollmentCode.issue(device, owner)

    assert raw_code == "000002"
    assert enrollment.code_hash == token_hash(raw_code)


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
    issue_kiosk_pin(device, owner)
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

    assert response.status_code == 200
    device = Device.objects.get(label="PILOT-03")
    assignment = device.assignments.get(unassigned_at__isnull=True)
    assert assignment.driver.internal_id == "D003"
    assert assignment.vehicle.registration == "WXY9012"
    assert device.kiosk_pin_hash
    assert len(response.context["pin"]) == 6
    assert "one_time_kiosk_pin" not in client.session
    assert response["Cache-Control"].startswith("no-store")


@pytest.mark.django_db
def test_dashboard_can_provision_device_with_attested_unapproved_hardware(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    qualification = HardwareQualification.objects.create(
        model_name="Attested Canary Tablet",
        firmware_version="pilot-build-1",
        android_version="12",
        security_patch_level="2026-08-05",
        tested_by=owner,
        test_date=timezone.localdate(),
        evidence_reference="restricted/hardware/attested-canary-tablet",
        measured_display_diagonal_inches=Decimal("10.00"),
    )
    client.force_login(owner)

    response = client.post(
        reverse("device-create"),
        {
            "device_label": "PILOT-ATTESTED-03",
            "hardware_qualification": str(qualification.pk),
            "driver_internal_id": "D004",
            "driver_name": "Example Driver",
            "vehicle_registration": "WXY9013",
        },
    )

    assert response.status_code == 200
    device = Device.objects.get(label="PILOT-ATTESTED-03")
    assert device.hardware_qualification_id == qualification.pk
    assert not qualification.approved_for_pilot


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
    issue_kiosk_pin(device, owner)
    pending_enrollment, _ = EnrollmentCode.issue(device, owner)
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
    pending_enrollment.refresh_from_db()
    assert pending_enrollment.is_usable is False


@pytest.mark.django_db
def test_reassignment_and_code_invalidation_roll_back_together(client, monkeypatch):
    owner = User.objects.create_user(
        "owner-reassign@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="PILOT-REASSIGN-ROLLBACK")
    driver = Driver.objects.create(internal_id="D-ROLLBACK", name="Old Driver")
    vehicle = Vehicle.objects.create(registration="ROLL1234")
    old_assignment = DeviceAssignment.objects.create(
        device=device,
        driver=driver,
        vehicle=vehicle,
    )
    issue_kiosk_pin(device, owner)
    pending_enrollment, _ = EnrollmentCode.issue(device, owner)
    client.force_login(owner)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("signage.views.audit", fail_audit)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post(
            reverse("device-reassign", args=[device.id]),
            {
                "driver_internal_id": "D-ROLLBACK-NEW",
                "driver_name": "New Driver",
                "vehicle_registration": "ROLL5678",
            },
        )

    old_assignment.refresh_from_db()
    pending_enrollment.refresh_from_db()
    assert old_assignment.unassigned_at is None
    assert pending_enrollment.is_usable is True
    assert not device.assignments.filter(
        driver__internal_id="D-ROLLBACK-NEW"
    ).exists()


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

    assert response.status_code == 200
    device.refresh_from_db()
    pin = response.context["pin"]
    assert len(pin) == 6
    algorithm, iterations, salt_hex, expected_hex = device.kiosk_pin_hash.split("$")
    import hashlib

    actual = hashlib.pbkdf2_hmac(
        "sha256", pin.encode(), bytes.fromhex(salt_hex), int(iterations)
    ).hex()
    assert algorithm == "pbkdf2_sha256"
    assert actual == expected_hex
    assert pin not in device.kiosk_pin_hash
    assert "one_time_kiosk_pin" not in client.session
    assert response["Cache-Control"].startswith("no-store")
    assert client.get(reverse("kiosk-pin")).status_code == 302


@pytest.mark.django_db
def test_enrollment_code_requires_kiosk_pin_verifier():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="PIN-REQUIRED-01")

    with pytest.raises(ValidationError, match="kiosk administrator PIN"):
        EnrollmentCode.issue(device, owner)


@pytest.mark.django_db
def test_legacy_code_is_unusable_without_kiosk_pin_verifier():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="LEGACY-NO-PIN-01")
    enrollment = EnrollmentCode.objects.create(
        device=device,
        code_hash=token_hash("123456"),
        expires_at=timezone.now() + timedelta(minutes=15),
        created_by=owner,
    )

    assert enrollment.is_usable is False


@pytest.mark.django_db
def test_dashboard_does_not_issue_enrollment_code_without_pin(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="NO-PIN-DASHBOARD-01")
    driver = Driver.objects.create(internal_id="NO-PIN", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="NOPIN01")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    client.force_login(owner)

    response = client.post(reverse("issue-enrollment", args=[device.id]))

    assert response.status_code == 302
    assert not EnrollmentCode.objects.filter(device=device).exists()


@pytest.mark.django_db
def test_enrollment_code_is_returned_once_without_session_persistence(client):
    device, _ = enrollment_fixture()
    owner = EnrollmentCode.objects.get(device=device).created_by
    client.force_login(owner)

    response = client.post(reverse("issue-enrollment", args=[device.id]))

    assert response.status_code == 200
    raw_code = response.context["code"]
    assert len(raw_code) == 6
    assert EnrollmentCode.objects.filter(
        device=device,
        code_hash=token_hash(raw_code),
        expires_at__gt=timezone.now(),
    ).exists()
    assert raw_code not in EnrollmentCode.objects.get(
        code_hash=token_hash(raw_code)
    ).code_hash
    assert "one_time_enrollment_code" not in client.session
    assert response["Cache-Control"].startswith("no-store")
    assert response["Pragma"] == "no-cache"
    assert client.get(reverse("enrollment-code")).status_code == 302


@pytest.mark.django_db
def test_disablement_invalidates_enrollment_and_blocks_new_codes(client):
    device, _ = enrollment_fixture()
    enrollment = EnrollmentCode.objects.get(device=device)
    owner = enrollment.created_by
    client.force_login(owner)

    response = client.post(reverse("device-disable", args=[device.id]))

    assert response.status_code == 302
    device.refresh_from_db()
    enrollment.refresh_from_db()
    assert device.status == Device.Status.DISABLED
    assert enrollment.is_usable is False
    with pytest.raises(ValidationError, match="enabled, assigned device"):
        EnrollmentCode.issue(device, owner)


@pytest.mark.django_db
def test_only_owner_can_issue_or_revoke_device_credentials(client):
    device, _ = enrollment_fixture()
    marketing = User.objects.create_user(
        "marketing-revoke@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    client.force_login(marketing)

    issue_response = client.post(reverse("issue-enrollment", args=[device.id]))
    revoke_response = client.post(
        reverse("device-credentials-revoke", args=[device.id])
    )

    assert issue_response.status_code == 403
    assert revoke_response.status_code == 403


@pytest.mark.django_db
def test_owner_credential_revoke_expires_pending_enrollment_and_access(client):
    device, _ = enrollment_fixture()
    owner = EnrollmentCode.objects.get(device=device).created_by
    credential, refresh_token = DeviceCredential.issue(device)
    access, _ = DeviceAccessToken.issue(credential)
    client.force_login(owner)

    response = client.post(reverse("device-credentials-revoke", args=[device.id]))

    assert response.status_code == 302
    credential.refresh_from_db()
    assert credential.revoked_at is not None
    assert not DeviceAccessToken.objects.filter(pk=access.pk).exists()
    assert EnrollmentCode.objects.get(device=device).is_usable is False
    refresh_response = client.post(
        reverse("device-token"),
        {"refresh_token": refresh_token},
        content_type="application/json",
    )
    assert refresh_response.status_code == 401


@pytest.mark.django_db
def test_kiosk_pin_reset_invalidates_outstanding_enrollment_code(client):
    device, _ = enrollment_fixture()
    enrollment = EnrollmentCode.objects.get(device=device)
    owner = enrollment.created_by
    client.force_login(owner)

    response = client.post(reverse("device-pin-reset", args=[device.id]))

    assert response.status_code == 200
    enrollment.refresh_from_db()
    assert enrollment.is_usable is False
