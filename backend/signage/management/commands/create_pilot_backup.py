import json
import os
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

BACKUP_PREFIX = "duducar-signage"


class Command(BaseCommand):
    help = "Create a compressed pilot backup of Django data and local media files."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default=settings.PILOT_BACKUP_ROOT)
        parser.add_argument(
            "--retain-days",
            type=int,
            default=settings.PILOT_BACKUP_RETENTION_DAYS,
        )
        parser.add_argument("--skip-media", action="store_true")
        parser.add_argument("--s3-bucket", default=settings.PILOT_BACKUP_S3_BUCKET)

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"]).expanduser().resolve()
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        archive_path = output_dir / f"{BACKUP_PREFIX}-{timestamp}.tar.gz"
        include_media = not options["skip_media"]

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            database_path = temporary_path / "database.json"
            metadata_path = temporary_path / "metadata.json"
            call_command(
                "dumpdata",
                "signage",
                indent=2,
                output=str(database_path),
                verbosity=0,
            )
            metadata_path.write_text(
                json.dumps(
                    {
                        "created_at": timezone.now().isoformat(),
                        "deployment_env": settings.DEPLOYMENT_ENV,
                        "time_zone": settings.TIME_ZONE,
                        "database_engine": settings.DATABASES["default"]["ENGINE"],
                        "media_included": include_media,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(metadata_path, arcname="metadata.json")
                archive.add(database_path, arcname="database.json")
                media_root = Path(settings.MEDIA_ROOT)
                if include_media and media_root.exists():
                    archive.add(media_root, arcname="media")

        os.chmod(archive_path, 0o600)
        self._prune_old_backups(output_dir, options["retain_days"])
        if not archive_path.exists():
            raise CommandError("Backup archive was not created.")
        call_command("verify_pilot_backup", str(archive_path), verbosity=0)
        if options["s3_bucket"]:
            import boto3

            key = f"application-backups/{archive_path.name}"
            boto3.client("s3").upload_file(str(archive_path), options["s3_bucket"], key)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Uploaded encrypted application backup to s3://"
                    f"{options['s3_bucket']}/{key}"
                )
            )
        self.stdout.write(self.style.SUCCESS(f"Created backup {archive_path}"))

    def _prune_old_backups(self, output_dir, retain_days):
        if retain_days < 1:
            raise CommandError("Backup retention must be at least one day.")
        cutoff = timezone.now() - timedelta(days=retain_days)
        for archive in output_dir.glob(f"{BACKUP_PREFIX}-*.tar.gz"):
            modified_at = datetime.fromtimestamp(
                archive.stat().st_mtime,
                tz=timezone.get_current_timezone(),
            )
            if modified_at < cutoff:
                archive.unlink()
