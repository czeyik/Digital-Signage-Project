import json
import tarfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Verify a pilot backup archive can be read and contains expected payloads."

    def add_arguments(self, parser):
        parser.add_argument("archive")

    def handle(self, *args, **options):
        archive_path = Path(options["archive"]).expanduser().resolve()
        if not archive_path.exists():
            raise CommandError(f"Backup archive does not exist: {archive_path}")
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = archive.getmembers()
                names = {member.name for member in members}
                self._reject_unsafe_members(members)
                for required in ("metadata.json", "database.json"):
                    if required not in names:
                        raise CommandError(f"Backup is missing {required}.")
                metadata_member = archive.extractfile("metadata.json")
                database_member = archive.extractfile("database.json")
                if metadata_member is None or database_member is None:
                    raise CommandError("Backup payload could not be read.")
                metadata = json.loads(metadata_member.read().decode("utf-8"))
                database = json.loads(database_member.read().decode("utf-8"))
                if not isinstance(database, list):
                    raise CommandError("Database payload is not a Django fixture list.")
                if metadata.get("media_included") and not any(
                    name == "media" or name.startswith("media/") for name in names
                ):
                    raise CommandError(
                        "Backup metadata says media is included, "
                        "but no media folder exists."
                    )
        except (tarfile.TarError, json.JSONDecodeError) as exc:
            raise CommandError(f"Backup verification failed: {exc}") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Verified backup {archive_path} with {len(database)} records."
            )
        )

    def _reject_unsafe_members(self, members):
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise CommandError(f"Backup contains unsafe path: {member.name}")
