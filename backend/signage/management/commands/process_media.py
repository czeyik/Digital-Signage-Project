import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from signage.models import MediaAsset
from signage.services import inspect_media


class Command(BaseCommand):
    help = "Scan and normalize quarantined media. Requires ClamAV and FFmpeg."

    def add_arguments(self, parser):
        parser.add_argument("--asset-id", type=uuid.UUID)
        parser.add_argument(
            "--allow-missing-clamav",
            action="store_true",
            help="Development only. Production must never use this option.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help=(
                "Compatibility mode for continuously polling a queue. "
                "Current production uses one-off --asset-id tasks."
            ),
        )
        parser.add_argument(
            "--sleep-seconds",
            type=int,
            default=10,
            help="Polling delay when --loop is enabled.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after processing this many assets. Zero means no limit.",
        )

    def handle(self, *args, **options):
        if (
            options["allow_missing_clamav"]
            and settings.DEPLOYMENT_ENV == "production"
        ):
            raise CommandError(
                "--allow-missing-clamav is forbidden in production."
            )
        if options["asset_id"] and options["loop"]:
            raise CommandError("--asset-id cannot be combined with --loop.")
        processed = 0
        while True:
            asset, reason = self._claim_asset(options["asset_id"])
            if not asset:
                if options["asset_id"]:
                    self.stdout.write(f"{options['asset_id']}: {reason}")
                    break
                if not options["loop"]:
                    break
                time.sleep(max(1, options["sleep_seconds"]))
                continue
            inspect_media(
                asset, require_malware_scanner=not options["allow_missing_clamav"]
            )
            processed += 1
            self.stdout.write(f"{asset.id}: {asset.status}")
            if options["limit"] and processed >= options["limit"]:
                break

    @transaction.atomic
    def _claim_asset(self, asset_id):
        now = timezone.now()
        assets = MediaAsset.objects.select_for_update(skip_locked=True)
        if asset_id:
            asset = assets.filter(pk=asset_id).first()
            if not asset:
                raise CommandError(f"Media asset does not exist: {asset_id}")
            if asset.status == MediaAsset.Status.PROCESSING:
                lease = asset.processing_lease_expires_at
                if lease and lease > now:
                    return None, "already processing"
            elif asset.status != MediaAsset.Status.QUARANTINED:
                return None, f"already {asset.status}"
        else:
            asset = (
                assets.filter(
                    Q(status=MediaAsset.Status.QUARANTINED)
                    | Q(
                        status=MediaAsset.Status.PROCESSING,
                        processing_lease_expires_at__isnull=True,
                    )
                    | Q(
                        status=MediaAsset.Status.PROCESSING,
                        processing_lease_expires_at__lte=now,
                    )
                )
                .order_by("created_at")
                .first()
            )
        if not asset:
            return None, "no eligible media"
        asset.status = MediaAsset.Status.PROCESSING
        asset.processing_attempts += 1
        asset.processing_token = uuid.uuid4()
        asset.processing_started_at = now
        asset.processing_lease_expires_at = now + timedelta(
            seconds=settings.MEDIA_PROCESSING_LEASE_SECONDS
        )
        asset.processing_finished_at = None
        asset.save(
            update_fields=[
                "status",
                "processing_attempts",
                "processing_token",
                "processing_started_at",
                "processing_lease_expires_at",
                "processing_finished_at",
                "updated_at",
            ]
        )
        return asset, "claimed"
