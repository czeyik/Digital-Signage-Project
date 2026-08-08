from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from signage.models import Playlist, User


def published_playlist(owner, *, name, version, starts_at=None):
    starts_at = starts_at or timezone.now() + timedelta(days=7)
    return Playlist.objects.create(
        name=name,
        version=version,
        status=Playlist.Status.PUBLISHED,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=7),
        published_at=timezone.now(),
        created_by=owner,
    )


@pytest.mark.django_db
def test_all_published_playlist_fields_are_immutable():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    playlist = published_playlist(owner, name="Locked week", version=1)
    playlist.published_at = timezone.now() + timedelta(minutes=1)

    with pytest.raises(ValidationError, match="Published playlist versions"):
        playlist.save(update_fields=["published_at"])


@pytest.mark.django_db
def test_cancelled_playlist_cannot_be_saved_or_changed_again():
    owner = User.objects.create_user(
        "owner@duducar.co",
        "A-very-long-password-123",
        role=User.Role.OWNER,
    )
    original = published_playlist(owner, name="Corrected week", version=1)
    replacement = published_playlist(
        owner,
        name="Corrected week",
        version=2,
        starts_at=original.starts_at,
    )
    original.status = Playlist.Status.CANCELLED
    original.superseded_by = replacement
    with pytest.raises(ValidationError, match="status and replacement together"):
        original.save(update_fields=["superseded_by"])
    original.refresh_from_db()
    original.status = Playlist.Status.CANCELLED
    original.superseded_by = replacement
    original.save(update_fields=["status", "superseded_by", "updated_at"])
    original.refresh_from_db()

    original.name = "Rewritten history"
    with pytest.raises(ValidationError, match="Cancelled playlist versions"):
        original.save(update_fields=["name"])

    original.refresh_from_db()
    with pytest.raises(ValidationError, match="Cancelled playlist versions"):
        original.save(update_fields=["updated_at"])
    with pytest.raises(ValidationError, match="cannot be deleted"):
        original.delete()
    with pytest.raises(ValidationError, match="cannot be deleted"):
        replacement.delete()
