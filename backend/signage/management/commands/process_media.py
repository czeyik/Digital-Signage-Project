from django.core.management.base import BaseCommand

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

    def handle(self, *args, **options):
        assets = MediaAsset.objects.filter(status=MediaAsset.Status.QUARANTINED)
        if options["asset_id"]:
            assets = assets.filter(pk=options["asset_id"])
        for asset in assets:
            inspect_media(
                asset, require_malware_scanner=not options["allow_missing_clamav"]
            )
            self.stdout.write(f"{asset.id}: {asset.status}")
