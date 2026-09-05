import csv
import uuid
from datetime import datetime, timedelta
from io import StringIO

import pytest
from django.urls import reverse
from django.utils import timezone

from signage.models import (
    AuditEvent,
    Device,
    MediaAsset,
    PlaybackBatch,
    PlaybackCorrection,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
    User,
)


def create_event(device, playlist, item, started_at, status):
    batch = PlaybackBatch.objects.create(
        id=uuid.uuid4(),
        device=device,
        playlist=playlist,
        loop_started_at=started_at,
        loop_ended_at=started_at + timedelta(seconds=15),
    )
    return PlaybackEvent.objects.create(
        id=uuid.uuid4(),
        batch=batch,
        playlist_item=item,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=15),
        duration_ms=15_000,
        status=status,
    )


@pytest.fixture
def report_data():
    owner = User.objects.create_user(
        "report-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    marketing = User.objects.create_user(
        "report-marketing@duducar.co",
        "A-very-long-password-123",
        role=User.Role.MARKETING,
    )
    devices = [Device.objects.create(label=f"REPORT-{number}") for number in range(3)]
    media = [
        MediaAsset.objects.create(
            business_name="Example",
            title=title,
            kind=MediaAsset.Kind.IMAGE,
            status=MediaAsset.Status.READY,
            source_file=f"quarantine/{title}.png",
            uploaded_by=owner,
        )
        for title in ("Poster", "Second")
    ]
    playlist = Playlist.objects.create(
        name="Report",
        starts_at=timezone.make_aware(datetime(2026, 9, 1)),
        ends_at=timezone.make_aware(datetime(2026, 9, 30)),
        created_by=owner,
    )
    items = [
        PlaylistItem.objects.create(playlist=playlist, media=asset, position=position)
        for position, asset in enumerate(media, start=1)
    ]
    return owner, marketing, devices, playlist, items


@pytest.mark.django_db
def test_report_page_is_available_to_marketing_users(client, report_data):
    _, marketing, devices, _, _ = report_data
    client.force_login(marketing)

    response = client.get(reverse("playback-report"))

    assert response.status_code == 200
    assert b"Export summary CSV" in response.content
    assert b"Export detailed CSV" in response.content
    assert devices[0].label.encode() in response.content


@pytest.mark.django_db
def test_summary_csv_aggregates_selected_devices_and_effective_status(
    client, report_data
):
    owner, marketing, devices, playlist, items = report_data
    day_one = timezone.make_aware(datetime(2026, 9, 4, 9))
    day_two = timezone.make_aware(datetime(2026, 9, 5, 9))

    create_event(
        devices[0], playlist, items[0], day_one, PlaybackEvent.Status.COMPLETED
    )
    corrected_to_failed = create_event(
        devices[1], playlist, items[0], day_one, PlaybackEvent.Status.COMPLETED
    )
    PlaybackCorrection.objects.create(
        event=corrected_to_failed,
        reason="Should not count",
        replacement_status=PlaybackEvent.Status.FAILED,
        created_by=owner,
    )
    corrected_to_completed = create_event(
        devices[1], playlist, items[0], day_two, PlaybackEvent.Status.FAILED
    )
    PlaybackCorrection.objects.create(
        event=corrected_to_completed,
        reason="Count after correction",
        replacement_status=PlaybackEvent.Status.COMPLETED,
        created_by=owner,
    )
    PlaybackCorrection.objects.create(
        event=corrected_to_completed,
        reason="Later note without a status change",
        created_by=owner,
    )
    create_event(
        devices[2], playlist, items[0], day_one, PlaybackEvent.Status.COMPLETED
    )
    create_event(
        devices[0], playlist, items[1], day_one, PlaybackEvent.Status.COMPLETED
    )
    create_event(
        devices[0],
        playlist,
        items[0],
        day_two + timedelta(days=1),
        PlaybackEvent.Status.COMPLETED,
    )
    client.force_login(marketing)

    response = client.get(
        reverse("playback-summary-csv"),
        {
            "date_from": "2026-09-04",
            "date_to": "2026-09-05",
            "devices": [str(devices[0].id), str(devices[1].id)],
        },
    )
    rows = list(csv.reader(StringIO(response.content.decode())))

    assert response.status_code == 200
    assert rows == [
        ["Media", "04/09/2026", "05/09/2026"],
        ["Example: Poster", "1", "1"],
        ["Example: Second", "1", "0"],
    ]
    audit = AuditEvent.objects.get(action="report.playback.summary.export")
    assert audit.actor == marketing
    assert audit.metadata == {
        "date_from": "2026-09-04",
        "date_to": "2026-09-05",
        "all_devices": False,
        "device_ids": [str(devices[0].id), str(devices[1].id)],
    }

    all_devices = client.get(
        reverse("playback-summary-csv"),
        {
            "date_from": "2026-09-04",
            "date_to": "2026-09-05",
            "all_devices": "on",
        },
    )
    all_rows = list(csv.reader(StringIO(all_devices.content.decode())))

    assert all_devices.status_code == 200
    assert all_rows[1] == ["Example: Poster", "2", "1"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "filters, message",
    [
        (
            {"date_from": "2026-09-05", "date_to": "2026-09-04", "all_devices": "on"},
            b"The end date must be on or after the start date.",
        ),
        (
            {"date_from": "2026-09-04", "date_to": "2026-09-05"},
            b"Select at least one device or choose all devices.",
        ),
        (
            {"date_from": "2025-09-04", "date_to": "2026-09-05", "all_devices": "on"},
            b"The date range cannot exceed 366 days.",
        ),
    ],
)
def test_summary_csv_rejects_invalid_filters(client, report_data, filters, message):
    _, marketing, _, _, _ = report_data
    client.force_login(marketing)

    response = client.get(reverse("playback-summary-csv"), filters)

    assert response.status_code == 400
    assert message in response.content
    assert not AuditEvent.objects.filter(
        action="report.playback.summary.export"
    ).exists()
