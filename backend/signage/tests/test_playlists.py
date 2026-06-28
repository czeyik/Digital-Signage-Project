from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from signage.models import MediaAsset, Playlist, PlaylistItem, User
from signage.services import delete_media_binary, publish_playlist


def next_monday_noon():
    now = timezone.localtime()
    days = (7 - now.weekday()) % 7
    if days == 0 and now.hour >= 12:
        days = 7
    return (now + timedelta(days=days)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )


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
    starts_at = next_monday_noon()
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
        duration_ms=10_000,
        uploaded_by=owner,
    )
    starts_at = next_monday_noon()
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
            duration_ms=10_000,
            uploaded_by=owner,
        )
        for number in (1, 2)
    ]
    starts_at = next_monday_noon()
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
        duration_ms=10_000,
        uploaded_by=owner,
    )
    starts_at = next_monday_noon()
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
def test_unreferenced_media_binary_deletion_preserves_metadata():
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
        duration_ms=10_000,
        uploaded_by=owner,
    )

    delete_media_binary(media, owner)

    media.refresh_from_db()
    assert media.status == MediaAsset.Status.ARCHIVED
    assert media.business_name == "Example"
    assert not media.source_file
    assert not media.normalized_file
