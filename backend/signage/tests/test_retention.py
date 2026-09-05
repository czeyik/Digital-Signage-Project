import uuid
from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from signage.models import (
    AuditEvent,
    Device,
    DeviceAccessToken,
    DeviceAssignment,
    DeviceCredential,
    DeviceLocationPoint,
    Driver,
    EnrollmentChallenge,
    EnrollmentCode,
    LoginThrottle,
    MediaAsset,
    PlaybackBatch,
    PlaybackCorrection,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
    User,
    Vehicle,
    token_hash,
)


@pytest.mark.django_db
def test_retention_uses_final_unassignment_date():
    driver = Driver.objects.create(internal_id="D001", name="Private Name")
    vehicle = Vehicle.objects.create(registration="WXY1234")
    old_device = Device.objects.create(label="OLD")
    recent_device = Device.objects.create(label="RECENT")
    DeviceAssignment.objects.create(
        device=old_device,
        driver=driver,
        vehicle=vehicle,
        assigned_at=timezone.now() - timedelta(days=800),
        unassigned_at=timezone.now() - timedelta(days=700),
    )
    recent = DeviceAssignment.objects.create(
        device=recent_device,
        driver=driver,
        vehicle=vehicle,
        assigned_at=timezone.now() - timedelta(days=100),
        unassigned_at=timezone.now() - timedelta(days=10),
    )

    call_command("apply_retention")
    driver.refresh_from_db()
    assert driver.anonymized_at is None

    recent.unassigned_at = timezone.now() - timedelta(days=400)
    recent.save(update_fields=["unassigned_at"])
    call_command("apply_retention")
    driver.refresh_from_db()
    vehicle.refresh_from_db()
    assert driver.name == "Anonymized driver"
    assert driver.anonymized_at is not None
    assert vehicle.registration.startswith("ANON-")


@pytest.mark.django_db
def test_retention_retries_anonymized_registration_collision(monkeypatch):
    old = timezone.now() - timedelta(days=400)
    driver = Driver.objects.create(internal_id="D-ROLLBACK", name="Private Name")
    vehicle = Vehicle.objects.create(registration="ROLLBACK-1")
    device = Device.objects.create(label="RETENTION-ROLLBACK")
    DeviceAssignment.objects.create(
        device=device,
        driver=driver,
        vehicle=vehicle,
        assigned_at=old - timedelta(days=1),
        unassigned_at=old,
    )
    Vehicle.objects.create(registration=f"ANON-{vehicle.pk}-abc123")
    suffixes = iter(["abc123", "def456"])
    monkeypatch.setattr(
        "signage.management.commands.apply_retention.secrets.token_hex",
        lambda bytes_count: next(suffixes),
    )

    call_command("apply_retention", verbosity=0)

    driver.refresh_from_db()
    vehicle.refresh_from_db()
    assert driver.name == "Anonymized driver"
    assert driver.anonymized_at is not None
    assert vehicle.registration == f"ANON-{vehicle.pk}-def456"
    assert vehicle.anonymized_at is not None
    assert AuditEvent.objects.filter(action="retention.apply").exists()


@pytest.mark.django_db
def test_retention_anonymizes_expired_assignment_sim_card_number():
    old = timezone.now() - timedelta(days=400)
    assignment = DeviceAssignment.objects.create(
        device=Device.objects.create(label="RETENTION-SIM"),
        driver=Driver.objects.create(internal_id="D-SIM", name="Private Name"),
        vehicle=Vehicle.objects.create(registration="SIM1234"),
        sim_card_number="+60129999999",
        assigned_at=old - timedelta(days=1),
        unassigned_at=old,
    )

    call_command("apply_retention", verbosity=0)

    assignment.refresh_from_db()
    assert assignment.sim_card_number == ""
    event = AuditEvent.objects.filter(action="retention.apply").latest("pk")
    assert event.metadata["sim_card_numbers_anonymized"] == 1


@pytest.mark.django_db
@override_settings(AUTH_ARTIFACT_RETENTION_DAYS=30)
def test_retention_deletes_expired_auth_artifacts():
    old = timezone.now() - timedelta(days=31)
    owner = User.objects.create_user(
        "retention-auth-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="RETENTION-AUTH")
    revoked, _ = DeviceCredential.issue(device)
    DeviceCredential.objects.filter(pk=revoked.pk).update(revoked_at=old)
    active, _ = DeviceCredential.issue(device)
    access = DeviceAccessToken.objects.create(
        credential=active,
        token_hash=token_hash("expired-access"),
        expires_at=old,
    )
    enrollment = EnrollmentCode.objects.create(
        device=device,
        code_hash=token_hash("expired-enrollment"),
        expires_at=old,
        created_by=owner,
    )
    challenge = EnrollmentChallenge.objects.create(
        enrollment=enrollment,
        request_hash=token_hash("expired-challenge"),
        android_id_hash=token_hash("android"),
        android_version="13",
        app_version="0.1.0",
        expires_at=old,
    )
    login = LoginThrottle.objects.create(key_hash=token_hash("old-login"))
    LoginThrottle.objects.filter(pk=login.pk).update(updated_at=old)
    from signage.models import ApiThrottle

    api = ApiThrottle.objects.create(key_hash=token_hash("old-api"))
    ApiThrottle.objects.filter(pk=api.pk).update(updated_at=old)
    session = Session.objects.create(
        session_key="expired-retention-session",
        session_data="e30:1w",
        expire_date=old,
    )

    call_command("apply_retention", verbosity=0)

    assert not DeviceCredential.objects.filter(pk=revoked.pk).exists()
    assert DeviceCredential.objects.filter(pk=active.pk).exists()
    assert not DeviceAccessToken.objects.filter(pk=access.pk).exists()
    assert not EnrollmentChallenge.objects.filter(pk=challenge.pk).exists()
    assert not EnrollmentCode.objects.filter(pk=enrollment.pk).exists()
    assert not LoginThrottle.objects.filter(pk=login.pk).exists()
    assert not ApiThrottle.objects.filter(pk=api.pk).exists()
    assert not Session.objects.filter(pk=session.pk).exists()


@pytest.mark.django_db
def test_retention_deletes_location_points_after_30_days():
    now = timezone.now()
    device = Device.objects.create(label="RETENTION-LOCATIONS")
    expired = DeviceLocationPoint.objects.create(
        id=uuid.uuid4(),
        device=device,
        recorded_at=now - timedelta(days=30, seconds=1),
        device_recorded_at=now - timedelta(days=30, seconds=1),
        latitude="3.139000",
        longitude="101.686900",
        accuracy_m="12.50",
        provider="gps",
        source="location_manager",
    )
    retained = DeviceLocationPoint.objects.create(
        id=uuid.uuid4(),
        device=device,
        recorded_at=now - timedelta(days=29),
        device_recorded_at=now - timedelta(days=29),
        latitude="3.140000",
        longitude="101.687900",
        accuracy_m="12.50",
        provider="gps",
        source="location_manager",
    )

    call_command("apply_retention", verbosity=0)

    assert not DeviceLocationPoint.objects.filter(pk=expired.pk).exists()
    assert DeviceLocationPoint.objects.filter(pk=retained.pk).exists()
    retention_audit = AuditEvent.objects.filter(action="retention.apply").first()
    assert retention_audit.metadata["location_points_deleted"] == 1


@pytest.mark.django_db
def test_retention_deletes_expired_playback_graphs_by_event_timestamp():
    now = timezone.now()
    old = now - timedelta(days=400)
    recent = now - timedelta(days=30)
    owner = User.objects.create_user(
        "retention-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="RETENTION-DEVICE")
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("retention.png", b"source"),
        normalized_file=SimpleUploadedFile("retention-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    playlist = Playlist.objects.create(
        name="Retention playlist",
        starts_at=old,
        ends_at=old + timedelta(days=7),
        created_by=owner,
    )
    item = PlaylistItem.objects.create(playlist=playlist, media=media, position=1)

    expired_batch = PlaybackBatch.objects.create(
        id=uuid.uuid4(),
        device=device,
        playlist=playlist,
        loop_started_at=old,
        loop_ended_at=old + timedelta(seconds=15),
    )
    expired_event = PlaybackEvent.objects.create(
        id=uuid.uuid4(),
        batch=expired_batch,
        playlist_item=item,
        started_at=old,
        ended_at=old + timedelta(seconds=15),
        duration_ms=15_000,
        status=PlaybackEvent.Status.COMPLETED,
    )
    expired_correction = PlaybackCorrection.objects.create(
        event=expired_event,
        reason="Old appended correction",
        created_by=owner,
    )
    PlaybackCorrection.objects.filter(pk=expired_correction.pk).update(created_at=old)

    corrected_batch = PlaybackBatch.objects.create(
        id=uuid.uuid4(),
        device=device,
        playlist=playlist,
        loop_started_at=old,
        loop_ended_at=old + timedelta(seconds=15),
    )
    corrected_event = PlaybackEvent.objects.create(
        id=uuid.uuid4(),
        batch=corrected_batch,
        playlist_item=item,
        started_at=old,
        ended_at=old + timedelta(seconds=15),
        duration_ms=15_000,
        status=PlaybackEvent.Status.COMPLETED,
    )
    recent_correction = PlaybackCorrection.objects.create(
        event=corrected_event,
        reason="Recent appended correction",
        created_by=owner,
    )

    recent_batch = PlaybackBatch.objects.create(
        id=uuid.uuid4(),
        device=device,
        playlist=playlist,
        loop_started_at=recent,
        loop_ended_at=recent + timedelta(seconds=15),
    )
    recent_event = PlaybackEvent.objects.create(
        id=uuid.uuid4(),
        batch=recent_batch,
        playlist_item=item,
        started_at=recent,
        ended_at=recent + timedelta(seconds=15),
        duration_ms=15_000,
        status=PlaybackEvent.Status.COMPLETED,
    )

    call_command("apply_retention", verbosity=0)

    assert not PlaybackCorrection.objects.filter(pk=expired_correction.pk).exists()
    assert not PlaybackEvent.objects.filter(pk=expired_event.pk).exists()
    assert not PlaybackBatch.objects.filter(pk=expired_batch.pk).exists()
    assert not PlaybackCorrection.objects.filter(pk=recent_correction.pk).exists()
    assert not PlaybackEvent.objects.filter(pk=corrected_event.pk).exists()
    assert not PlaybackBatch.objects.filter(pk=corrected_batch.pk).exists()
    assert PlaybackEvent.objects.filter(pk=recent_event.pk).exists()
    assert PlaybackBatch.objects.filter(pk=recent_batch.pk).exists()
    retention_audit = AuditEvent.objects.filter(action="retention.apply").first()
    assert retention_audit.metadata["playback_corrections_deleted"] == 2
    assert retention_audit.metadata["playback_events_deleted"] == 2
    assert retention_audit.metadata["playback_batches_deleted"] == 2
