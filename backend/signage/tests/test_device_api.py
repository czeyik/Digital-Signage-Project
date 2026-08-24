import csv
import gzip
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO

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
    DeviceHeartbeat,
    DeviceOperationalEvent,
    Driver,
    HardwareQualification,
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


def post_heartbeat(client, access, **extra):
    payload = {
        "recorded_at": timezone.now().isoformat(),
        "screen_on": True,
        "free_storage_bytes": 3 * 1024 * 1024 * 1024,
        "app_version": "0.1.0",
        "android_version": "13",
        "playback_active": True,
    }
    payload.update(extra)
    return client.post(
        reverse("device-heartbeat"),
        payload,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )


def post_operational_event(client, access, payload):
    return client.post(
        reverse("device-operational-event"),
        payload,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access}",
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


def set_manifest_media_url(monkeypatch, item, url):
    monkeypatch.setattr(item.media.normalized_file.storage, "url", lambda name: url)


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
@override_settings(
    DEPLOYMENT_ENV="production",
    AWS_S3_CUSTOM_DOMAIN="media.example.cloudfront.net",
)
def test_production_sync_requires_a_current_approved_hardware_qualification(
    client, provisioned_device, monkeypatch
):
    device, _, item, access = provisioned_device
    set_manifest_media_url(
        monkeypatch,
        item,
        "https://media.example.cloudfront.net/validated/poster.png?Expires=1",
    )
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access}"}

    absent_qualification = client.get(reverse("device-sync"), **headers)
    assert absent_qualification.status_code == 200
    assert absent_qualification.json()["mode"] == "maintenance"

    owner = User.objects.get(role=User.Role.OWNER)
    qualification = HardwareQualification(
        model_name="Qualified Canary Tablet",
        firmware_version="pilot-1",
        android_version="13",
        security_patch_level="2026-08-05",
        tested_by=owner,
        test_date=timezone.localdate(),
        evidence_reference="restricted/hardware/qualified-canary-tablet",
        measured_display_diagonal_inches=Decimal("10.00"),
        approved_for_pilot=True,
        **{field: True for field in HardwareQualification.REQUIRED_PASS_FIELDS},
    )
    qualification.save()
    device.hardware_qualification = qualification
    device.hardware_model = qualification.model_name
    device.hardware_firmware_version = qualification.firmware_version
    device.hardware_security_patch = qualification.security_patch_level
    device.save(
        update_fields=[
            "hardware_qualification",
            "hardware_model",
            "hardware_firmware_version",
            "hardware_security_patch",
            "updated_at",
        ]
    )

    qualified = client.get(reverse("device-sync"), **headers)
    assert qualified.status_code == 200
    assert qualified.json()["mode"] == "play"

    qualification.approved_for_pilot = False
    qualification.save()
    maintenance = client.get(reverse("device-sync"), **headers)

    assert maintenance.status_code == 200
    assert maintenance.json()["mode"] == "maintenance"
    device.refresh_from_db()
    assert device.status == Device.Status.ACTIVE
    assert device.last_sync_at is not None
    assert DeviceCredential.objects.get(device=device).revoked_at is None


@pytest.mark.django_db
@override_settings(AWS_S3_CUSTOM_DOMAIN="media.example.cloudfront.net")
def test_sync_manifest_exposes_and_enforces_the_configured_media_origin(
    client, provisioned_device, monkeypatch
):
    device, _, item, access = provisioned_device
    set_manifest_media_url(
        monkeypatch,
        item,
        "https://media.example.cloudfront.net/validated/poster.png?Expires=1",
    )

    response = client.get(
        reverse("device-sync"), HTTP_AUTHORIZATION=f"Bearer {access}"
    )

    assert response.status_code == 200
    manifest = response.json()["playlist"]
    assert manifest["media_origin"] == "media.example.cloudfront.net"
    assert manifest["items"][0]["download_url"].startswith(
        "https://media.example.cloudfront.net/"
    )
    device.refresh_from_db()
    successful_sync_at = device.last_sync_at

    set_manifest_media_url(
        monkeypatch,
        item,
        "https://untrusted.example.test/validated/poster.png?Expires=1",
    )
    rejected = client.get(
        reverse("device-sync"), HTTP_AUTHORIZATION=f"Bearer {access}"
    )

    assert rejected.status_code == 500
    assert rejected.json()["error"]["detail"] == "Media delivery is unavailable."
    device.refresh_from_db()
    assert device.last_sync_at == successful_sync_at


@pytest.mark.django_db
@override_settings(AWS_S3_CUSTOM_DOMAIN="https://media.example.cloudfront.net")
def test_sync_manifest_rejects_a_non_hostname_media_origin(
    client, provisioned_device
):
    _, _, _, access = provisioned_device

    response = client.get(
        reverse("device-sync"), HTTP_AUTHORIZATION=f"Bearer {access}"
    )

    assert response.status_code == 500
    assert response.json()["error"]["detail"] == "Media delivery is unavailable."


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
@pytest.mark.parametrize(
    "reason",
    [
        "external_power_lost",
        "planned_shutdown",
        "app_restart_or_unexpected_exit",
        "credential_rejected",
        "server_forbidden",
    ],
)
def test_playback_batch_accepts_a_known_interruption_category(
    client, provisioned_device, reason
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    payload["events"][0].update(
        {
            "duration_ms": 1_000,
            "status": "interrupted",
            "failure_reason": reason,
        }
    )

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 201
    event = PlaybackBatch.objects.get().events.get()
    assert event.status == "interrupted"
    assert event.failure_reason == reason


@pytest.mark.django_db
@pytest.mark.parametrize(
    "reason", ["decode_failure", "missing_file", "start_timeout", "playback_timeout"]
)
def test_playback_batch_accepts_a_known_failure_category(
    client, provisioned_device, reason
):
    _, playlist, item, access = provisioned_device
    payload = playback_payload(playlist, item)
    payload["events"][0].update(
        {
            "duration_ms": 1_000,
            "status": "failed",
            "failure_reason": reason,
        }
    )

    response = post_playback_batch(client, payload, access)

    assert response.status_code == 201
    assert PlaybackBatch.objects.get().events.get().failure_reason == reason


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
    content = b"".join(report.streaming_content).decode()

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

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert Alert.objects.filter(code="repeated_device_authentication").exists()


@pytest.mark.django_db
def test_disabled_device_access_and_refresh_tokens_are_unauthorized(
    client, provisioned_device
):
    device, _, _, _ = provisioned_device
    credential, refresh_token = DeviceCredential.issue(device)
    _, access_token = DeviceAccessToken.issue(credential)
    Device.objects.filter(pk=device.pk).update(status=Device.Status.DISABLED)

    refresh_response = client.post(
        reverse("device-token"),
        {"refresh_token": refresh_token},
        content_type="application/json",
    )
    access_response = client.get(
        reverse("device-sync"),
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert refresh_response.status_code == 401
    assert access_response.status_code == 401
    assert refresh_response["WWW-Authenticate"] == "Bearer"
    assert access_response["WWW-Authenticate"] == "Bearer"


@pytest.mark.django_db
def test_operational_event_upload_is_idempotent(client, provisioned_device):
    _, _, _, access = provisioned_device
    payload = {
        "id": str(uuid.uuid4()),
        "kind": "forced_queue_loss",
        "recorded_at": timezone.now().isoformat(),
        "details": {
            "removed_batches": 1,
            "estimated_removed_bytes": 1_000,
            "target_removed_bytes": 2_000,
        },
    }
    first = post_operational_event(client, access, payload)
    replay = post_operational_event(client, access, payload)
    altered_replay = post_operational_event(
        client,
        access,
        {
            **payload,
            "details": {
                "removed_batches": 2,
                "estimated_removed_bytes": 1_000,
                "target_removed_bytes": 2_000,
            },
        },
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert altered_replay.status_code == 400


@pytest.mark.django_db
def test_new_operational_event_kinds_require_their_strict_details(
    client, provisioned_device
):
    _, _, _, access = provisioned_device
    recorded_at = timezone.now().isoformat()

    planned = post_operational_event(
        client,
        access,
        {
            "id": str(uuid.uuid4()),
            "kind": "planned_shutdown",
            "recorded_at": recorded_at,
            "details": {},
        },
    )
    abnormal_responses = [
        post_operational_event(
            client,
            access,
            {
                "id": str(uuid.uuid4()),
                "kind": "abnormal_app_exit",
                "recorded_at": recorded_at,
                "details": {"reason": reason},
            },
        )
        for reason in (
            "crash",
            "native_crash",
            "anr",
            "initialization_failure",
            "low_memory",
            "excessive_resource_usage",
            "freezer_termination",
        )
    ]
    missing_planned_details = post_operational_event(
        client,
        access,
        {
            "id": str(uuid.uuid4()),
            "kind": "planned_shutdown",
            "recorded_at": recorded_at,
        },
    )
    invalid_planned = post_operational_event(
        client,
        access,
        {
            "id": str(uuid.uuid4()),
            "kind": "planned_shutdown",
            "recorded_at": recorded_at,
            "details": {"reason": "operator"},
        },
    )
    invalid_abnormal = post_operational_event(
        client,
        access,
        {
            "id": str(uuid.uuid4()),
            "kind": "abnormal_app_exit",
            "recorded_at": recorded_at,
            "details": {"reason": "unknown", "trace": "do-not-store"},
        },
    )

    assert planned.status_code == 201
    assert all(response.status_code == 201 for response in abnormal_responses)
    assert missing_planned_details.status_code == 400
    assert invalid_planned.status_code == 400
    assert invalid_abnormal.status_code == 400
    assert DeviceOperationalEvent.objects.filter(
        kind=DeviceOperationalEvent.Kind.PLANNED_SHUTDOWN
    ).count() == 1


@pytest.mark.django_db
def test_replacement_failure_event_requires_a_known_stage_and_exact_fields(
    client, provisioned_device
):
    _, playlist, _, access = provisioned_device
    payload = {
        "id": str(uuid.uuid4()),
        "kind": "replacement_failed",
        "recorded_at": timezone.now().isoformat(),
        "details": {"playlist_id": str(playlist.id), "stage": "activation"},
    }

    valid = post_operational_event(client, access, payload)
    invalid = post_operational_event(
        client,
        access,
        {
            **payload,
            "id": str(uuid.uuid4()),
            "details": {"playlist_id": str(playlist.id), "stage": "retry"},
        },
    )
    unexpected = post_operational_event(
        client,
        access,
        {**payload, "id": str(uuid.uuid4()), "untrusted": True},
    )

    assert valid.status_code == 201
    assert invalid.status_code == 400
    assert unexpected.status_code == 400


@pytest.mark.django_db
def test_heartbeat_preserves_legacy_power_telemetry_and_allows_nulls(
    client, provisioned_device
):
    device, _, _, access = provisioned_device

    legacy = post_heartbeat(
        client,
        access,
        external_power=True,
        charging=False,
    )
    legacy_inverse = post_heartbeat(
        client,
        access,
        external_power=False,
        charging=True,
    )
    omitted = post_heartbeat(client, access)
    explicit_null = post_heartbeat(
        client,
        access,
        external_power=None,
        charging=None,
    )

    assert legacy.status_code == 200
    assert legacy_inverse.status_code == 200
    assert omitted.status_code == 200
    assert explicit_null.status_code == 200
    heartbeats = list(DeviceHeartbeat.objects.order_by("id"))
    assert (heartbeats[0].external_power, heartbeats[0].charging) == (True, False)
    assert (heartbeats[1].external_power, heartbeats[1].charging) == (False, True)
    assert (heartbeats[2].external_power, heartbeats[2].charging) == (None, None)
    assert (heartbeats[3].external_power, heartbeats[3].charging) == (None, None)
    device.refresh_from_db()
    assert device.last_seen_at == heartbeats[-1].received_at


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("external_power", "false"),
        ("external_power", 1),
        ("charging", "true"),
        ("charging", 0),
    ],
)
def test_heartbeat_rejects_non_boolean_power_telemetry(
    client, provisioned_device, field, value
):
    _, _, _, access = provisioned_device

    response = post_heartbeat(client, access, **{field: value})

    assert response.status_code == 400
    assert field in str(response.json()["error"]["detail"])
    assert not DeviceHeartbeat.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [("screen_on", "true"), ("playback_active", 1)],
)
def test_heartbeat_requires_boolean_display_state(
    client, provisioned_device, field, value
):
    _, _, _, access = provisioned_device

    response = post_heartbeat(client, access, **{field: value})

    assert response.status_code == 400
    assert field in str(response.json()["error"]["detail"])
    assert not DeviceHeartbeat.objects.exists()


@pytest.mark.django_db
def test_stale_heartbeat_cannot_regress_device_aggregate_state(
    client, provisioned_device
):
    device, playlist, _, access = provisioned_device
    newer = timezone.now() - timedelta(minutes=1)
    older = newer - timedelta(minutes=1)
    newer_sync = newer - timedelta(seconds=10)
    newer_playback = newer - timedelta(seconds=5)

    assert post_heartbeat(
        client,
        access,
        recorded_at=newer.isoformat(),
        app_version="0.1.0",
        android_version="13",
        active_playlist_id=str(playlist.id),
        last_successful_sync_at=newer_sync.isoformat(),
        last_playback_at=newer_playback.isoformat(),
    ).status_code == 200
    assert post_heartbeat(
        client,
        access,
        recorded_at=older.isoformat(),
        app_version="stale-version",
        android_version="12",
        active_playlist_id=None,
        last_successful_sync_at=older.isoformat(),
        last_playback_at=older.isoformat(),
    ).status_code == 200

    device.refresh_from_db()
    assert device.last_heartbeat_recorded_at == newer
    assert device.app_version == "0.1.0"
    assert device.android_version == "13"
    assert device.current_playlist_id == playlist.id
    assert device.last_sync_at == newer_sync
    assert device.last_playback_at == newer_playback


@pytest.mark.django_db
def test_csv_neutralizes_leading_whitespace_formula_cells(client, provisioned_device):
    _, playlist, item, access = provisioned_device
    MediaAsset.objects.filter(pk=item.media_id).update(title="\t=HYPERLINK(\"bad\")")
    response = post_playback_batch(client, playback_payload(playlist, item), access)
    assert response.status_code == 201
    client.force_login(User.objects.get(email="owner@duducar.co"))

    report = client.get(reverse("playback-csv"))
    rows = list(csv.reader(StringIO(b"".join(report.streaming_content).decode())))

    assert rows[1][5] == "'\t=HYPERLINK(\"bad\")"


@pytest.mark.django_db
def test_low_battery_alert_opens_and_escalates_without_auto_deescalation(
    client, provisioned_device
):
    device, _, _, access = provisioned_device

    assert post_heartbeat(client, access, battery_percent=21).status_code == 200
    assert not Alert.objects.filter(device=device, code="low_battery").exists()

    assert post_heartbeat(client, access, battery_percent=20).status_code == 200
    alert = Alert.objects.get(device=device, code="low_battery")
    assert alert.severity == Alert.Severity.WARNING

    assert post_heartbeat(client, access, battery_percent=10).status_code == 200
    alert.refresh_from_db()
    assert alert.severity == Alert.Severity.CRITICAL
    assert Alert.objects.filter(
        device=device,
        code="low_battery",
        acknowledged_at__isnull=True,
    ).count() == 1

    assert post_heartbeat(client, access, battery_percent=15).status_code == 200
    alert.refresh_from_db()
    assert alert.severity == Alert.Severity.CRITICAL
    assert alert.acknowledged_at is None


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
                "details": {
                    "removed_batches": 1,
                    "estimated_removed_bytes": 1_000,
                    "target_removed_bytes": 2_000,
                },
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
