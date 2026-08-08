import gzip
import json
import uuid
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
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
    PlaybackBatch,
    Playlist,
    PlaylistItem,
    User,
    Vehicle,
)


def post_playback_batch(client, payload, access, **headers):
    body = gzip.compress(json.dumps(payload).encode("utf-8"), mtime=0)
    return client.post(
        reverse("playback-batch"),
        body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
        HTTP_CONTENT_ENCODING="gzip",
        **headers,
    )


def playback_payload(playlist, item, **extra):
    ended_at = timezone.now()
    started_at = ended_at - timedelta(seconds=10)
    payload = {
        "id": str(uuid.uuid4()),
        "playlist_id": str(playlist.id),
        "loop_started_at": started_at.isoformat(),
        "loop_ended_at": ended_at.isoformat(),
        "captured_offline": False,
        "events": [
            {
                "id": str(uuid.uuid4()),
                "playlist_item_id": str(item.id),
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "duration_ms": 10_000,
                "status": "completed",
            }
        ],
    }
    payload.update(extra)
    return payload


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
    DeviceAssignment.objects.create(
        device=device,
        driver=driver,
        vehicle=vehicle,
        assigned_at=timezone.now() - timedelta(minutes=1),
    )
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
def test_valid_gzip_playback_batch_is_idempotent(client, provisioned_device):
    device, playlist, item, access = provisioned_device
    batch_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    ended_at = timezone.now()
    started_at = ended_at - timedelta(seconds=10)
    payload = {
        "id": batch_id,
        "playlist_id": str(playlist.id),
        "loop_started_at": started_at.isoformat(),
        "loop_ended_at": ended_at.isoformat(),
        "captured_offline": True,
        "events": [
            {
                "id": event_id,
                "playlist_item_id": str(item.id),
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "duration_ms": 10_000,
                "status": "completed",
            }
        ],
    }
    first = post_playback_batch(client, payload, access)
    second = post_playback_batch(client, payload, access)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


@pytest.mark.django_db
def test_playback_batch_accepts_a_known_interruption_category(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    payload["events"][0].update(
        {
            "duration_ms": 1_000,
            "status": "interrupted",
            "failure_reason": "external_power_lost",
        }
    )

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 201
    event = PlaybackBatch.objects.get().events.get()
    assert event.status == "interrupted"
    assert event.failure_reason == "external_power_lost"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "events",
    [None, {}, "event", 1, [], [None], ["event"], [[]]],
)
def test_playback_batch_rejects_invalid_event_collection_shapes(
    client, provisioned_device, events
):
    _, playlist, item, access = provisioned_device

    response = post_playback_batch(
        client,
        playback_payload(playlist, item, events=events),
        access,
    )

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field",
    [
        "id",
        "playlist_id",
        "loop_started_at",
        "loop_ended_at",
        "captured_offline",
        "events",
    ],
)
def test_playback_batch_requires_every_batch_field(
    client, provisioned_device, field
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    del payload[field]

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field",
    ["id", "playlist_item_id", "started_at", "ended_at", "duration_ms", "status"],
)
def test_playback_batch_requires_every_completed_event_field(
    client, provisioned_device, field
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    del payload["events"][0][field]

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "duration_ms",
    [None, True, 10_000.0, "10000", -1, 2_147_483_648, 10**100],
)
def test_playback_batch_rejects_non_integer_or_out_of_range_durations(
    client, provisioned_device, duration_ms
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    payload["events"][0]["duration_ms"] = duration_ms

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("captured_offline", [None, 0, 1, "false", [], {}])
def test_playback_batch_requires_a_strict_offline_boolean(
    client, provisioned_device, captured_offline
):
    _, playlist, item, access = provisioned_device

    response = post_playback_batch(
        client,
        playback_payload(
            playlist,
            item,
            captured_offline=captured_offline,
        ),
        access,
    )

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "not-a-uuid"),
        ("playlist_item_id", "not-a-uuid"),
        ("status", ["completed"]),
        ("failure_reason", {"category": "decode_failure"}),
        ("failure_reason", "decode\x00failure"),
        ("failure_reason", "A" * 65),
    ],
)
def test_playback_batch_rejects_malformed_event_scalar_fields(
    client, provisioned_device, field, value
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    payload["events"][0][field] = value

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        True,
        1,
        {},
        [],
        "",
        "not-a-timestamp",
        "0000-01-01T00:00:00Z",
        "2026-01-01T00:00:00+24:00",
    ],
)
def test_playback_batch_rejects_malformed_timestamps_without_server_errors(
    client, provisioned_device, timestamp
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    payload["events"][0]["started_at"] = timestamp

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case",
    [
        "loop_end_before_start",
        "event_end_before_start",
        "event_outside_loop",
        "duration_exceeds_timestamp_interval",
        "completed_duration_mismatch",
        "completed_with_failure_reason",
        "interrupted_without_failure_reason",
        "interrupted_with_unknown_failure_reason",
        "failed_with_interruption_reason",
        "minimum_datetime_boundary",
    ],
)
def test_playback_batch_rejects_internally_inconsistent_evidence(
    client, provisioned_device, case
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    event = payload["events"][0]
    started_at = timezone.now() - timedelta(seconds=20)
    ended_at = started_at + timedelta(seconds=10)
    payload["loop_started_at"] = started_at.isoformat()
    payload["loop_ended_at"] = ended_at.isoformat()
    event["started_at"] = started_at.isoformat()
    event["ended_at"] = ended_at.isoformat()

    if case == "loop_end_before_start":
        payload["loop_ended_at"] = (started_at - timedelta(seconds=1)).isoformat()
    elif case == "event_end_before_start":
        event["ended_at"] = (started_at - timedelta(seconds=1)).isoformat()
    elif case == "event_outside_loop":
        event["started_at"] = (ended_at + timedelta(seconds=6)).isoformat()
        event["ended_at"] = (ended_at + timedelta(seconds=16)).isoformat()
    elif case == "duration_exceeds_timestamp_interval":
        event["started_at"] = (ended_at - timedelta(seconds=1)).isoformat()
    elif case == "completed_duration_mismatch":
        event["duration_ms"] = 10_001
    elif case == "completed_with_failure_reason":
        event["failure_reason"] = "decode_failure"
    elif case == "interrupted_without_failure_reason":
        event["status"] = "interrupted"
        event["duration_ms"] = 1_000
    elif case == "interrupted_with_unknown_failure_reason":
        event["status"] = "interrupted"
        event["duration_ms"] = 1_000
        event["failure_reason"] = "unknown_failure"
    elif case == "failed_with_interruption_reason":
        event["status"] = "failed"
        event["duration_ms"] = 1_000
        event["failure_reason"] = "external_power_lost"
    elif case == "minimum_datetime_boundary":
        payload["loop_started_at"] = "0001-01-01T00:00:00+00:00"
        payload["loop_ended_at"] = "0001-01-01T00:00:01+00:00"
        event["started_at"] = "0001-01-01T00:00:00+00:00"
        event["ended_at"] = "0001-01-01T00:00:01+00:00"

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
def test_playback_batch_rejects_draft_playlist_evidence(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    Playlist.objects.filter(pk=playlist.pk).update(
        status=Playlist.Status.DRAFT,
        published_at=None,
    )

    response = post_playback_batch(
        client,
        playback_payload(playlist, item),
        access,
    )

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
def test_playback_batch_rejects_evidence_before_playlist_start(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    future_start = timezone.now() + timedelta(hours=1)
    Playlist.objects.filter(pk=playlist.pk).update(
        starts_at=future_start,
        ends_at=future_start + timedelta(days=7),
    )
    playlist.refresh_from_db()

    response = post_playback_batch(
        client,
        playback_payload(playlist, item),
        access,
    )

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
def test_playback_batch_rejects_batch_id_reuse_with_different_evidence(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)

    first = post_playback_batch(client, payload, access)
    payload["captured_offline"] = True
    conflict = post_playback_batch(client, payload, access)

    assert first.status_code == 201
    assert conflict.status_code == 400
    assert PlaybackBatch.objects.count() == 1
    assert PlaybackBatch.objects.get().captured_offline is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        b"not-a-gzip-stream",
        gzip.compress(b'{"id":"truncated"}', mtime=0)[:-4],
    ],
)
def test_playback_batch_rejects_malformed_or_truncated_gzip(
    client, provisioned_device, body
):
    _, _, _, access = provisioned_device

    response = client.post(
        reverse("playback-batch"),
        body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
        HTTP_CONTENT_ENCODING="gzip",
    )

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
def test_playback_batch_rejects_malformed_json_inside_valid_gzip(
    client, provisioned_device
):
    _, _, _, access = provisioned_device

    response = client.post(
        reverse("playback-batch"),
        gzip.compress(b"{not-json", mtime=0),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
        HTTP_CONTENT_ENCODING="gzip",
    )

    assert response.status_code == 400
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@override_settings(PLAYBACK_BATCH_MAX_COMPRESSED_BYTES=32)
def test_playback_batch_rejects_oversized_compressed_body(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    body = gzip.compress(
        json.dumps(playback_payload(playlist, item)).encode("utf-8"), mtime=0
    )
    assert len(body) > 32

    response = client.post(
        reverse("playback-batch"),
        body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
        HTTP_CONTENT_ENCODING="gzip",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "playback_batch_too_large"
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
@override_settings(
    PLAYBACK_BATCH_MAX_COMPRESSED_BYTES=1024,
    PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES=256,
)
def test_playback_batch_rejects_oversized_decompressed_body(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    raw = json.dumps(
        playback_payload(playlist, item, padding="A" * 4096)
    ).encode("utf-8")
    body = gzip.compress(raw, mtime=0)
    assert len(body) < 1024
    assert len(raw) > 256

    response = client.post(
        reverse("playback-batch"),
        body,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
        HTTP_CONTENT_ENCODING="gzip",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "playback_batch_too_large"
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
def test_playback_batch_rejects_uncompressed_json(client, provisioned_device):
    _, playlist, item, access = provisioned_device

    response = client.post(
        reverse("playback-batch"),
        playback_payload(playlist, item),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_content_encoding"
    assert not PlaybackBatch.objects.exists()


@pytest.mark.django_db
def test_event_identifier_collision_is_not_reported_as_accepted(
    client, provisioned_device
):
    _, playlist, item, access = provisioned_device
    event_id = str(uuid.uuid4())
    ended_at = timezone.now()
    started_at = ended_at - timedelta(seconds=10)

    def payload():
        return {
            "id": str(uuid.uuid4()),
            "playlist_id": str(playlist.id),
            "loop_started_at": started_at.isoformat(),
            "loop_ended_at": ended_at.isoformat(),
            "captured_offline": False,
            "events": [
                {
                    "id": event_id,
                    "playlist_item_id": str(item.id),
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "duration_ms": 10_000,
                    "status": "completed",
                }
            ],
        }

    first = post_playback_batch(client, payload(), access)
    collision = post_playback_batch(client, payload(), access)

    assert first.status_code == 201
    assert collision.status_code == 400
    assert collision.json()["error"]["detail"]


@pytest.mark.django_db
def test_csv_filters_preserve_driver_privacy_and_finalization_notice(
    client, provisioned_device
):
    device, playlist, item, access = provisioned_device
    ended_at = timezone.now()
    started_at = ended_at - timedelta(seconds=10)
    response = post_playback_batch(
        client,
        {
            "id": str(uuid.uuid4()),
            "playlist_id": str(playlist.id),
            "loop_started_at": started_at.isoformat(),
            "loop_ended_at": ended_at.isoformat(),
            "captured_offline": True,
            "events": [
                {
                    "id": str(uuid.uuid4()),
                    "playlist_item_id": str(item.id),
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "duration_ms": 10_000,
                    "status": "completed",
                }
            ],
        },
        access,
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
    ended_at = timezone.now()
    started_at = ended_at - timedelta(seconds=10)

    response = post_playback_batch(
        client,
        {
            "id": str(uuid.uuid4()),
            "playlist_id": str(playlist.id),
            "loop_started_at": started_at.isoformat(),
            "loop_ended_at": ended_at.isoformat(),
            "captured_offline": False,
            "events": [
                {
                    "id": str(uuid.uuid4()),
                    "playlist_item_id": str(item.id),
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "duration_ms": 10_000,
                    "status": "completed",
                }
            ],
        },
        access,
    )

    assert response.status_code == 400
    assert "every playlist entry" in str(response.json()["error"]["detail"])


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


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("endpoint", "timestamp_field", "payload"),
    [
        (
            "device-heartbeat",
            "recorded_at",
            {
                "screen_on": True,
                "external_power": True,
                "charging": True,
                "free_storage_bytes": 3 * 1024 * 1024 * 1024,
                "app_version": "1.0.0",
                "android_version": "12",
            },
        ),
        (
            "device-operational-event",
            "recorded_at",
            {
                "id": str(uuid.uuid4()),
                "kind": "forced_queue_loss",
                "details": {"removed_batches": 1},
            },
        ),
    ],
)
def test_device_api_rejects_timestamps_too_far_ahead_of_server_time(
    client,
    provisioned_device,
    endpoint,
    timestamp_field,
    payload,
):
    _, _, _, access = provisioned_device
    payload[timestamp_field] = (timezone.now() + timedelta(minutes=6)).isoformat()

    response = client.post(
        reverse(endpoint),
        payload,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )

    assert response.status_code == 400
    assert "ahead of server time" in str(response.json()["error"]["detail"])


@pytest.mark.django_db
def test_playback_batch_rejects_future_event_timestamps(client, provisioned_device):
    _, playlist, item, access = provisioned_device
    now = timezone.now()
    future = now + timedelta(minutes=6)

    response = post_playback_batch(
        client,
        {
            "id": str(uuid.uuid4()),
            "playlist_id": str(playlist.id),
            "loop_started_at": now.isoformat(),
            "loop_ended_at": now.isoformat(),
            "captured_offline": False,
            "events": [
                {
                    "id": str(uuid.uuid4()),
                    "playlist_item_id": str(item.id),
                    "started_at": future.isoformat(),
                    "ended_at": future.isoformat(),
                    "duration_ms": 10_000,
                    "status": "completed",
                }
            ],
        },
        access,
    )

    assert response.status_code == 400
    assert "ahead of server time" in str(response.json()["error"]["detail"])
