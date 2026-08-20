from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from signage.models import MediaDeletion
from signage.services import process_media_deletion


class Command(BaseCommand):
    help = "Retry a bounded batch of archived media object deletions."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **options):
        limit = options["limit"]
        maximum = settings.MEDIA_RECONCILE_MAX_ASSETS
        if not 1 <= limit <= maximum:
            raise CommandError(f"--limit must be between 1 and {maximum}.")
        deletion_ids = list(
            MediaDeletion.objects.filter(completed_at__isnull=True)
            .order_by("created_at")
            .values_list("pk", flat=True)[:limit]
        )
        completed = sum(
            process_media_deletion(deletion_id) for deletion_id in deletion_ids
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Reconciled "
                f"{len(deletion_ids)} media deletions; completed {completed}."
            )
        )
