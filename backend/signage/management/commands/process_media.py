import time

from django.core.management.base import BaseCommand
from django.db import transaction

from signage.models import MediaAsset
from signage.services import inspect_media


class Command(BaseCommand):
    help = "Scan and normalize quarantined media. Requires ClamAV and FFmpeg."

    def add_arguments(self, parser):
        parser.add_argument("--asset-id")
        parser.add_argument(
            "--allow-missing-clamav",
            action="store_true",
            help="Development only. Production must never use this option.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Continuously poll for quarantined media for a worker container.",
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
        processed = 0
        while True:
            asset = self._claim_asset(options["asset_id"])
            if not asset:
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
        assets = (
            MediaAsset.objects.select_for_update(skip_locked=True)
            .filter(status=MediaAsset.Status.QUARANTINED)
            .order_by("created_at")
        )
        if asset_id:
            assets = assets.filter(pk=asset_id)
        asset = assets.first()
        if not asset:
            return None
        asset.status = MediaAsset.Status.PROCESSING
        asset.save(update_fields=["status", "updated_at"])
        return asset
