from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections, connection, connections, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from signage.models import (
    Alert,
    MediaAsset,
    MediaDeletion,
    Playlist,
    PlaylistItem,
    User,
)
from signage.services import (
    active_playlist,
    delete_media_binary,
    next_playlist_transition_at,
    publish_playlist,
)

TEST_STATICFILES_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}


@pytest.fixture(autouse=True)
def ready_media_delivery_is_valid(monkeypatch):
    """Scheduling tests use intentionally minimal ready-media records."""

    monkeypatch.setattr(
        "signage.services.validate_ready_media_delivery",
        lambda asset: None,
    )


def next_schedule_start():
    return (timezone.now() + timedelta(days=1, minutes=7)).replace(microsecond=0)


def current_schedule_start():
    return (timezone.now() - timedelta(hours=1, minutes=7)).replace(microsecond=0)


@pytest.mark.django_db
def test_playlist_accepts_arbitrary_schedule_boundaries():
    owner = User.objects.create_user(
        "arbitrary-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    playlist = Playlist(
        name="Arbitrary schedule",
        version=1,
        starts_at=timezone.now() + timedelta(minutes=23),
        ends_at=timezone.now() + timedelta(hours=2, minutes=41),
        created_by=owner,
    )

    playlist.full_clean()


@pytest.mark.django_db
def test_next_playlist_transition_exposes_the_scheduled_boundary():
    owner = User.objects.create_user(
        "transition-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    starts_at = next_schedule_start()
    Playlist.objects.create(
        name="Next scheduled week",
        version=1,
        status=Playlist.Status.PUBLISHED,
        published_at=timezone.now(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )

    assert next_playlist_transition_at() == starts_at


@pytest.mark.django_db
def test_next_playlist_transition_exposes_the_active_schedule_end():
    owner = User.objects.create_user(
        "ending-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    starts_at = timezone.now() - timedelta(hours=1)
    ends_at = timezone.now() + timedelta(minutes=37)
    Playlist.objects.create(
        name="Ending schedule",
        version=1,
        status=Playlist.Status.PUBLISHED,
        published_at=timezone.now(),
        starts_at=starts_at,
        ends_at=ends_at,
        created_by=owner,
    )

    assert next_playlist_transition_at() == ends_at


@pytest.mark.django_db
def test_schedule_evaluation_accepts_a_replacement_beyond_seven_days():
    owner = User.objects.create_user(
        "replacement-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    now = timezone.now()
    active_end = now + timedelta(days=1)
    Playlist.objects.create(
        name="Current arbitrary schedule",
        version=1,
        status=Playlist.Status.PUBLISHED,
        published_at=now,
        starts_at=now - timedelta(days=1),
        ends_at=active_end,
        created_by=owner,
    )
    Playlist.objects.create(
        name="Later arbitrary replacement",
        version=1,
        status=Playlist.Status.PUBLISHED,
        published_at=now,
        starts_at=active_end + timedelta(days=10),
        ends_at=active_end + timedelta(days=11, hours=3),
        created_by=owner,
    )

    call_command("evaluate_playlists")

    assert not Alert.objects.filter(code="missing_playlist_replacement").exists()


@pytest.mark.django_db
def test_only_ready_media_can_be_published():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        source_file=SimpleUploadedFile("poster.png", b"not-used"),
        uploaded_by=owner,
    )
    starts_at = next_schedule_start()
    playlist = Playlist.objects.create(
        name="Pilot week",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    PlaylistItem.objects.create(playlist=playlist, media=media, position=1)

    with pytest.raises(ValidationError):
        publish_playlist(playlist, owner)


@pytest.mark.django_db
def test_published_playlist_items_are_immutable():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("poster.png", b"source"),
        normalized_file=SimpleUploadedFile("poster-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    starts_at = next_schedule_start()
    playlist = Playlist.objects.create(
        name="Pilot week",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    item = PlaylistItem.objects.create(playlist=playlist, media=media, position=1)
    publish_playlist(playlist, owner)
    item.position = 2
    with pytest.raises(ValidationError):
        item.save()


@pytest.mark.django_db
def test_draft_playlist_can_be_reordered_from_dashboard(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = [
        MediaAsset.objects.create(
            business_name="Example",
            title=f"Poster {number}",
            kind=MediaAsset.Kind.IMAGE,
            status=MediaAsset.Status.READY,
            source_file=SimpleUploadedFile(f"poster-{number}.png", b"source"),
            normalized_file=SimpleUploadedFile(f"ready-{number}.png", b"ready"),
            duration_ms=15_000,
            uploaded_by=owner,
        )
        for number in (1, 2)
    ]
    starts_at = next_schedule_start()
    playlist = Playlist.objects.create(
        name="Reorder week",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    first = PlaylistItem.objects.create(playlist=playlist, media=media[0], position=1)
    second = PlaylistItem.objects.create(playlist=playlist, media=media[1], position=2)
    client.force_login(owner)

    response = client.post(
        reverse("playlist-detail", args=[playlist.id]),
        {"action": "reorder", "order": f"{second.id},{first.id}"},
    )

    assert response.status_code == 302
    assert list(playlist.items.order_by("position").values_list("id", flat=True)) == [
        second.id,
        first.id,
    ]


@pytest.mark.django_db
@override_settings(STORAGES=TEST_STATICFILES_STORAGES)
def test_playlist_detail_uses_csp_safe_drag_script(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = [
        MediaAsset.objects.create(
            business_name="Example",
            title=f"Poster {number}",
            kind=MediaAsset.Kind.IMAGE,
            status=MediaAsset.Status.READY,
            source_file=SimpleUploadedFile(f"poster-{number}.png", b"source"),
            normalized_file=SimpleUploadedFile(f"ready-{number}.png", b"ready"),
            duration_ms=15_000,
            uploaded_by=owner,
        )
        for number in (1, 2)
    ]
    starts_at = next_schedule_start()
    playlist = Playlist.objects.create(
        name="Reorder week",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    first = PlaylistItem.objects.create(playlist=playlist, media=media[0], position=1)
    second = PlaylistItem.objects.create(playlist=playlist, media=media[1], position=2)
    client.force_login(owner)

    response = client.get(reverse("playlist-detail", args=[playlist.id]))

    assert response.status_code == 200
    assert "script-src 'self'" in response["Content-Security-Policy"]
    assert b"signage/playlist_detail.js" in response.content
    assert b"data-playlist-sortable" in response.content
    assert f'value="{first.id},{second.id}"'.encode() in response.content
    assert f'form="remove-item-{first.id}"'.encode() in response.content
    assert b"document.querySelector" not in response.content


@pytest.mark.django_db
def test_playlist_list_marks_current_published_window_active(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("poster.png", b"source"),
        normalized_file=SimpleUploadedFile("poster-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    starts_at = current_schedule_start()
    playlist = Playlist.objects.create(
        name="Current week",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    PlaylistItem.objects.create(playlist=playlist, media=media, position=1)
    publish_playlist(playlist, owner)
    client.force_login(owner)

    response = client.get(reverse("playlist-list"))

    assert response.status_code == 200
    assert b"Current week" in response.content
    assert b"Active now" in response.content


@pytest.mark.django_db
def test_corrected_playlist_publish_cancels_prior_version_and_hides_history(client):
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("poster.png", b"source"),
        normalized_file=SimpleUploadedFile("poster-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    starts_at = next_schedule_start()
    first = Playlist.objects.create(
        name="Corrected week",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    PlaylistItem.objects.create(playlist=first, media=media, position=1)
    publish_playlist(first, owner)
    second = Playlist.objects.create(
        name="Corrected week",
        version=2,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    PlaylistItem.objects.create(playlist=second, media=media, position=1)

    publish_playlist(second, owner)
    third = Playlist.objects.create(
        name="Corrected week",
        version=3,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    PlaylistItem.objects.create(playlist=third, media=media, position=1)

    publish_playlist(third, owner)

    first.refresh_from_db()
    second.refresh_from_db()
    third.refresh_from_db()
    assert first.status == Playlist.Status.CANCELLED
    assert first.superseded_by == second
    assert second.status == Playlist.Status.CANCELLED
    assert second.superseded_by == third
    assert third.status == Playlist.Status.PUBLISHED
    client.force_login(owner)
    list_response = client.get(reverse("playlist-list"))
    detail_response = client.get(reverse("playlist-detail", args=[third.id]))
    assert list_response.status_code == 200
    assert list_response.content.count(b"Corrected week") == 1
    assert b"v1" in detail_response.content
    assert b"v2" in detail_response.content
    assert b"Cancelled" in detail_response.content


@pytest.mark.django_db
def test_media_binary_deletion_is_blocked_when_referenced_by_future_playlist():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("poster.png", b"source"),
        normalized_file=SimpleUploadedFile("poster-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    starts_at = next_schedule_start()
    playlist = Playlist.objects.create(
        name="Future week",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    PlaylistItem.objects.create(playlist=playlist, media=media, position=1)

    with pytest.raises(ValidationError):
        delete_media_binary(media, owner)


@pytest.mark.django_db
def test_unreferenced_media_binary_deletion_queues_a_durable_outbox_entry():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("poster.png", b"source"),
        normalized_file=SimpleUploadedFile("poster-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )

    delete_media_binary(media, owner)

    media.refresh_from_db()
    assert media.status == MediaAsset.Status.ARCHIVED
    assert media.business_name == "Example"
    assert media.source_file
    assert media.normalized_file
    deletion = MediaDeletion.objects.get(asset=media)
    assert deletion.source_name == media.source_file.name
    assert deletion.normalized_name == media.normalized_file.name
    assert deletion.completed_at is None


@pytest.mark.django_db
def test_media_deletion_outbox_rolls_back_with_the_archive_transaction():
    owner = User.objects.create_user(
        "rollback-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Rollback poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("rollback.png", b"source"),
        duration_ms=15_000,
        uploaded_by=owner,
    )

    with transaction.atomic():
        delete_media_binary(media, owner)
        assert MediaDeletion.objects.filter(asset=media).exists()
        transaction.set_rollback(True)

    media.refresh_from_db()
    assert media.status == MediaAsset.Status.READY
    assert not MediaDeletion.objects.filter(asset=media).exists()


@pytest.mark.django_db
def test_overlapping_schedules_publish_and_latest_start_takes_precedence():
    owner = User.objects.create_user(
        "overlap-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("overlap.png", b"source"),
        normalized_file=SimpleUploadedFile("overlap-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    starts_at = current_schedule_start()
    first = Playlist.objects.create(
        name="Campaign A",
        version=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    second = Playlist.objects.create(
        name="Campaign B",
        version=1,
        starts_at=starts_at + timedelta(minutes=17),
        ends_at=starts_at + timedelta(days=7),
        created_by=owner,
    )
    PlaylistItem.objects.create(playlist=first, media=media, position=1)
    PlaylistItem.objects.create(playlist=second, media=media, position=1)
    publish_playlist(first, owner)

    publish_playlist(second, owner)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == Playlist.Status.PUBLISHED
    assert second.status == Playlist.Status.PUBLISHED
    assert active_playlist() == second


@pytest.mark.django_db(transaction=True)
def test_postgres_serializes_concurrent_overlapping_publications():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL-specific publication locking test")
    owner = User.objects.create_user(
        "concurrent-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("concurrent.png", b"source"),
        normalized_file=SimpleUploadedFile("concurrent-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    starts_at = next_schedule_start()
    playlist_ids = []
    for name in ("Concurrent A", "Concurrent B"):
        playlist = Playlist.objects.create(
            name=name,
            version=1,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=7),
            created_by=owner,
        )
        PlaylistItem.objects.create(playlist=playlist, media=media, position=1)
        playlist_ids.append(playlist.pk)

    start_together = Barrier(2)

    def publish(playlist_id):
        close_old_connections()
        try:
            start_together.wait(timeout=5)
            publish_playlist(
                Playlist.objects.get(pk=playlist_id),
                User.objects.get(pk=owner.pk),
            )
        except ValidationError:
            return "rejected"
        finally:
            connections.close_all()
        return "published"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(publish, playlist_ids))

    assert outcomes == ["published", "published"]
    assert Playlist.objects.filter(status=Playlist.Status.PUBLISHED).count() == 2


@pytest.mark.django_db
def test_urgent_replacement_covers_only_current_window_and_preserves_schedules():
    owner = User.objects.create_user(
        "urgent-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("urgent.png", b"source"),
        normalized_file=SimpleUploadedFile("urgent-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    current_start = current_schedule_start()
    future_start = current_start + timedelta(days=7)

    def playlist(name, starts_at):
        created = Playlist.objects.create(
            name=name,
            version=1,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=7),
            created_by=owner,
        )
        PlaylistItem.objects.create(playlist=created, media=media, position=1)
        return created

    current = playlist("Current schedule", current_start)
    future = playlist("Future schedule", future_start)
    urgent = playlist("Current emergency", current_start)
    future_urgent = playlist("Future emergency", future_start)
    publish_playlist(current, owner)
    publish_playlist(future, owner)

    publish_playlist(urgent, owner, urgent=True)

    current.refresh_from_db()
    future.refresh_from_db()
    urgent.refresh_from_db()
    assert current.status == Playlist.Status.PUBLISHED
    assert future.status == Playlist.Status.PUBLISHED
    assert urgent.status == Playlist.Status.PUBLISHED
    assert urgent.is_urgent is True

    with pytest.raises(ValidationError, match="current time"):
        publish_playlist(future_urgent, owner, urgent=True)


@pytest.mark.django_db
def test_media_dashboard_requires_explicit_binary_deletion_confirmation(
    client,
    tmp_path,
    settings,
):
    settings.MEDIA_ROOT = tmp_path
    owner = User.objects.create_user(
        "delete-owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    media = MediaAsset.objects.create(
        business_name="Example",
        title="Poster",
        kind=MediaAsset.Kind.IMAGE,
        status=MediaAsset.Status.READY,
        source_file=SimpleUploadedFile("delete.png", b"source"),
        normalized_file=SimpleUploadedFile("delete-ready.png", b"ready"),
        duration_ms=15_000,
        uploaded_by=owner,
    )
    client.force_login(owner)
    url = reverse("media-delete", args=[media.id])

    assert client.post(url).status_code == 302
    media.refresh_from_db()
    assert media.status == MediaAsset.Status.READY
    assert media.source_file
    assert media.normalized_file

    assert client.post(url, {"confirm": "delete"}).status_code == 302
    media.refresh_from_db()
    assert media.status == MediaAsset.Status.ARCHIVED
    assert media.source_file
    assert media.normalized_file
    assert MediaDeletion.objects.filter(asset=media, completed_at__isnull=True).exists()
