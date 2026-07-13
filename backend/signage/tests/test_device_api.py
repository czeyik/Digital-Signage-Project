import uuid
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from signage.api import active_playlist
from signage.models import (
    Alert,
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
def test_event_identifier_collision_is_not_reported_as_accepted(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    event_id = str(uuid.uuid4())
    now = timezone.now().isoformat()

    def payload():
        return {
            "id": str(uuid.uuid4()),
            "playlist_id": str(playlist.id),
            "loop_started_at": now,
            "loop_ended_at": now,
            "captured_offline": False,
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
        reverse("playback-batch"), payload(), content_type="application/json", **headers
    )
    collision = client.post(
        reverse("playback-batch"), payload(), content_type="application/json", **headers
    )

    assert first.status_code == 201
    assert collision.status_code == 400
    assert collision.json()["error"]["detail"]


@pytest.mark.django_db
def test_csv_filters_preserve_driver_privacy_and_finalization_notice(
    client, provisioned_device
):
    device, playlist, item, access = provisioned_device
    now = timezone.now()
    response = client.post(
        reverse("playback-batch"),
        {
            "id": str(uuid.uuid4()),
            "playlist_id": str(playlist.id),
            "loop_started_at": now.isoformat(),
            "loop_ended_at": now.isoformat(),
            "captured_offline": True,
            "events": [
                {
                    "id": str(uuid.uuid4()),
                    "playlist_item_id": str(item.id),
                    "started_at": now.isoformat(),
                    "ended_at": now.isoformat(),
                    "duration_ms": 10_000,
                    "status": "completed",
                }
            ],
        },
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )
    assert response.status_code == 201
    client.force_login(User.objects.get(email="owner@duducar.co"))

    report = client.get(
        reverse("playback-csv"),
        {
            "device": device.label,
            "vehicle": "WXY1234",
            "driver": "D001",
            "campaign": "Example",
            "status": "completed",
            "offline": "true",
        },
    )
    content = report.content.decode()

    assert report.status_code == 200
    assert "Example Driver" not in content
    assert "D001" in content
    assert "provisional" in content
    assert "not independently audited or tamper-proof" in content


@pytest.mark.django_db
def test_csv_rejects_invalid_date_filter(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    client.force_login(owner)

    response = client.get(reverse("playback-csv"), {"date_from": "not-a-date"})

    assert response.status_code == 400


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


@pytest.mark.django_db
def test_playback_batch_requires_every_playlist_entry(client, provisioned_device):
    _, _, _, access = provisioned_device
    owner = User.objects.get(email="owner@duducar.co")
    media = [
        MediaAsset.objects.create(
            business_name="Example",
            title=f"Poster {number}",
            kind=MediaAsset.Kind.IMAGE,
            status=MediaAsset.Status.READY,
            source_file=SimpleUploadedFile(f"poster-{number}.png", b"source"),
            normalized_file=SimpleUploadedFile(f"poster-ready-{number}.png", b"ready"),
            duration_ms=10_000,
            uploaded_by=owner,
        )
        for number in (1, 2)
    ]
    playlist = Playlist.objects.create(
        name="Two item pilot",
        version=1,
        starts_at=timezone.now() - timedelta(hours=1),
        ends_at=timezone.now() + timedelta(days=6),
        created_by=owner,
    )
    item = PlaylistItem.objects.create(playlist=playlist, media=media[0], position=1)
    PlaylistItem.objects.create(playlist=playlist, media=media[1], position=2)
    playlist.status = Playlist.Status.PUBLISHED
    playlist.published_at = timezone.now()
    playlist.save(update_fields=["status", "published_at"])
    now = timezone.now().isoformat()

    response = client.post(
        reverse("playback-batch"),
        {
            "id": str(uuid.uuid4()),
            "playlist_id": str(playlist.id),
            "loop_started_at": now,
            "loop_ended_at": now,
            "captured_offline": False,
            "events": [
                {
                    "id": str(uuid.uuid4()),
                    "playlist_item_id": str(item.id),
                    "started_at": now,
                    "ended_at": now,
                    "duration_ms": 10_000,
                    "status": "completed",
                }
            ],
        },
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )

    assert response.status_code == 400
    assert "every playlist entry" in response.json()["error"]["detail"][0]


@pytest.mark.django_db
def test_invalid_device_refresh_creates_security_alert(client):
    for _ in range(6):
        response = client.post(
            reverse("device-token"),
            {"refresh_token": "invalid-token"},
            content_type="application/json",
        )

    assert response.status_code == 403
    assert Alert.objects.filter(code="repeated_device_authentication").exists()


@pytest.mark.django_db
def test_operational_event_upload_is_idempotent(client, provisioned_device):
    _, _, _, access = provisioned_device
    payload = {
        "id": str(uuid.uuid4()),
        "kind": "forced_queue_loss",
        "recorded_at": timezone.now().isoformat(),
        "details": {"removed_batches": 1},
    }
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access}"}

    first = client.post(
        reverse("device-operational-event"),
        payload,
        content_type="application/json",
        **headers,
    )
    replay = client.post(
        reverse("device-operational-event"),
        payload,
        content_type="application/json",
        **headers,
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
