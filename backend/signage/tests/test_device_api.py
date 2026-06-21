import uuid
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from signage.api import active_playlist
from signage.models import (
    Device,
    DeviceAccessToken,
    DeviceAssignment,
    DeviceCredential,
    Driver,
    MediaAsset,
    Playlist,
    PlaylistItem,
    User,
    Vehicle,
)


@pytest.fixture
def provisioned_device():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    device = Device.objects.create(label="PILOT-01", status=Device.Status.ACTIVE)
    driver = Driver.objects.create(internal_id="D001", name="Example Driver")
    vehicle = Vehicle.objects.create(registration="WXY1234")
    DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
    credential, _ = DeviceCredential.issue(device)
    _, access = DeviceAccessToken.issue(credential)
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("poster.png", b"source"),
        normalized_file=SimpleUploadedFile("poster-ready.png", b"ready"),
        duration_ms=10_000,
        uploaded_by=owner,
    )
    playlist = Playlist.objects.create(
        name="Pilot",
        version=1,
        starts_at=timezone.now() - timedelta(hours=1),
        ends_at=timezone.now() + timedelta(days=6),
        created_by=owner,
    )
    item = PlaylistItem.objects.create(playlist=playlist, media=media, position=1)
    playlist.status = Playlist.Status.PUBLISHED
    playlist.published_at = timezone.now()
    playlist.save(update_fields=["status", "published_at"])
    return device, playlist, item, access


@pytest.mark.django_db
def test_duplicate_playback_batch_is_idempotent(client, provisioned_device):
    device, playlist, item, access = provisioned_device
    batch_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    now = timezone.now().isoformat()
    payload = {
        "id": batch_id,
        "playlist_id": str(playlist.id),
        "loop_started_at": now,
        "loop_ended_at": now,
        "captured_offline": True,
        "events": [
            {
                "id": event_id,
                "playlist_item_id": str(item.id),
                "started_at": now,
                "ended_at": now,
                "duration_ms": 10_000,
                "status": "completed",
            }
        ],
    }
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
    first = client.post(
        reverse("playback-batch"), payload, content_type="application/json", **headers
    )
    second = client.post(
        reverse("playback-batch"), payload, content_type="application/json", **headers
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


@pytest.mark.django_db
def test_future_playlist_is_not_selected_early():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    future = Playlist.objects.create(
        name="Future",
        version=1,
        status=Playlist.Status.PUBLISHED,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=14),
        published_at=timezone.now(),
        created_by=owner,
    )
    assert active_playlist() is None
    assert future.pk is not None
