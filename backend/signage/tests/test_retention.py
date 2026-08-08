import uuid
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone

from signage.models import (
    AuditEvent,
    Device,
    DeviceAssignment,
    Driver,
    MediaAsset,
    PlaybackBatch,
    PlaybackCorrection,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
    User,
    Vehicle,
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
def test_retention_rolls_back_all_changes_when_anonymization_fails():
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
    Vehicle.objects.create(registration=f"ANON-{vehicle.pk}")

    with pytest.raises(IntegrityError):
        call_command("apply_retention", verbosity=0)

    driver.refresh_from_db()
    vehicle.refresh_from_db()
    assert driver.name == "Private Name"
    assert driver.anonymized_at is None
    assert vehicle.registration == "ROLLBACK-1"
    assert vehicle.anonymized_at is None
    assert not AuditEvent.objects.filter(action="retention.apply").exists()


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
        duration_ms=10_000,
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
        loop_ended_at=old + timedelta(seconds=10),
    )
    expired_event = PlaybackEvent.objects.create(
        id=uuid.uuid4(),
        batch=expired_batch,
        playlist_item=item,
        started_at=old,
        ended_at=old + timedelta(seconds=10),
        duration_ms=10_000,
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
        loop_ended_at=old + timedelta(seconds=10),
    )
    corrected_event = PlaybackEvent.objects.create(
        id=uuid.uuid4(),
        batch=corrected_batch,
        playlist_item=item,
        started_at=old,
        ended_at=old + timedelta(seconds=10),
        duration_ms=10_000,
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
        loop_ended_at=recent + timedelta(seconds=10),
    )
    recent_event = PlaybackEvent.objects.create(
        id=uuid.uuid4(),
        batch=recent_batch,
        playlist_item=item,
        started_at=recent,
        ended_at=recent + timedelta(seconds=10),
        duration_ms=10_000,
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
