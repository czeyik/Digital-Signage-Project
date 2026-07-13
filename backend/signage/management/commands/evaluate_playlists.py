from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from signage.models import Alert, Playlist
from signage.services import open_alert


class Command(BaseCommand):
    help = "Warn when no published weekly replacement covers the next schedule."

    def handle(self, *args, **options):
        now = timezone.now()
        active = (
            Playlist.objects.filter(
                status=Playlist.Status.PUBLISHED,
                starts_at__lte=now,
                ends_at__gt=now,
                is_urgent=False,
            )
            .order_by("-starts_at", "-version")
            .first()
        )
        if active:
            replacement_exists = Playlist.objects.filter(
                status=Playlist.Status.PUBLISHED,
                starts_at__gte=active.ends_at,
                starts_at__lt=active.ends_at + timedelta(days=7),
                is_urgent=False,
            ).exists()
            if not replacement_exists and active.ends_at <= now + timedelta(days=2):
                open_alert(
                    None,
                    "missing_playlist_replacement",
                    Alert.Severity.WARNING,
                    "No published weekly replacement exists; current content "
                    "will continue.",
                )
        self.stdout.write(self.style.SUCCESS("Playlist schedule evaluated."))
