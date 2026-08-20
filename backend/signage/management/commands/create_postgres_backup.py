import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

BACKUP_PREFIX = "duducar-signage-postgres"
REMOTE_SUCCESS_RECEIPT = f"{BACKUP_PREFIX}-last-remote-success.json"


class Command(BaseCommand):
    help = "Create, validate, hash, and upload a PostgreSQL custom-format backup."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default=settings.PILOT_BACKUP_ROOT)
        parser.add_argument("--s3-bucket", default=settings.PILOT_BACKUP_S3_BUCKET)
        parser.add_argument(
            "--retain-days",
            type=int,
            default=settings.PILOT_BACKUP_RETENTION_DAYS,
        )
        parser.add_argument(
            "--max-local-archives",
            type=int,
            default=settings.PILOT_BACKUP_MAX_LOCAL_ARCHIVES,
        )

    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        if "postgresql" not in database["ENGINE"]:
            raise CommandError("PostgreSQL is required for this backup command.")
        if not options["s3_bucket"]:
            raise CommandError("An S3 backup bucket is required.")
        if options["retain_days"] < 1:
            raise CommandError("Backup retention must be at least one day.")
        if options["max_local_archives"] < 1:
            raise CommandError("At least one local backup archive must be retained.")
        pg_dump = shutil.which("pg_dump")
        pg_restore = shutil.which("pg_restore")
        if not pg_dump or not pg_restore:
            raise CommandError("pg_dump and pg_restore are required.")

        output_dir = Path(options["output_dir"]).expanduser().resolve()
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(output_dir, 0o700)
        timestamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        archive_path = output_dir / f"{BACKUP_PREFIX}-{timestamp}.dump"
        digest_path = archive_path.with_suffix(".dump.sha256")
        temporary_path = None

        try:
            temporary_path = self._temporary_path(output_dir)
            self._dump_database(pg_dump, database, temporary_path)
            self._validate_archive(pg_restore, temporary_path)
            digest = self._sha256(temporary_path)
            os.replace(temporary_path, archive_path)
            os.chmod(archive_path, 0o600)
            digest_path.write_text(
                f"{digest}  {archive_path.name}\n",
                encoding="ascii",
            )
            os.chmod(digest_path, 0o600)
            remote_receipt = self._upload(
                options["s3_bucket"],
                archive_path,
                digest_path,
                digest,
            )
            self._write_remote_success_receipt(output_dir, remote_receipt)
            self._prune_old_backups(
                output_dir,
                options["retain_days"],
                options["max_local_archives"],
            )
        except subprocess.SubprocessError as exc:
            raise CommandError(
                "PostgreSQL backup or archive validation failed."
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created and validated PostgreSQL backup {archive_path.name}."
            )
        )

    def _temporary_path(self, output_dir):
        handle = tempfile.NamedTemporaryFile(
            prefix=".postgres-backup-",
            suffix=".dump",
            dir=output_dir,
            delete=False,
        )
        path = Path(handle.name)
        handle.close()
        return path

    def _database_environment(self, database):
        environment = os.environ.copy()
        environment["PGPASSWORD"] = str(database.get("PASSWORD", ""))
        sslmode = database.get("OPTIONS", {}).get("sslmode")
        if sslmode:
            environment["PGSSLMODE"] = str(sslmode)
        return environment

    def _dump_database(self, executable, database, destination):
        command = [
            executable,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(destination),
            "--host",
            str(database.get("HOST", "")),
            "--port",
            str(database.get("PORT", 5432)),
            "--username",
            str(database.get("USER", "")),
            str(database.get("NAME", "")),
        ]
        subprocess.run(  # noqa: S603 - executable is resolved with shutil.which.
            command,
            check=True,
            capture_output=True,
            env=self._database_environment(database),
        )

    def _validate_archive(self, executable, archive_path):
        subprocess.run(  # noqa: S603 - executable is resolved with shutil.which.
            [executable, "--list", str(archive_path)],
            check=True,
            capture_output=True,
        )

    def _sha256(self, archive_path):
        digest = hashlib.sha256()
        with archive_path.open("rb") as archive:
            for chunk in iter(lambda: archive.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _upload(self, bucket, archive_path, digest_path, digest):
        archive_key = f"database-backups/{archive_path.name}"
        digest_key = f"database-backups/{digest_path.name}"
        try:
            client = boto3.client("s3")
            client.upload_file(
                str(archive_path),
                bucket,
                archive_key,
                ExtraArgs={
                    "ChecksumAlgorithm": "SHA256",
                    "Metadata": {"sha256": digest},
                },
            )
            client.upload_file(
                str(digest_path),
                bucket,
                digest_key,
                ExtraArgs={
                    "ChecksumAlgorithm": "SHA256",
                    "ContentType": "text/plain",
                    "Metadata": {"sha256": digest},
                },
            )
            return self._verify_remote_upload(
                client,
                bucket,
                archive_key,
                archive_path,
                digest_key,
                digest_path,
                digest,
            )
        except (BotoCoreError, ClientError) as exc:
            raise CommandError("PostgreSQL backup upload failed.") from exc

    def _verify_remote_upload(
        self,
        client,
        bucket,
        archive_key,
        archive_path,
        digest_key,
        digest_path,
        digest,
    ):
        try:
            archive_head = client.head_object(
                Bucket=bucket,
                Key=archive_key,
                ChecksumMode="ENABLED",
            )
            digest_head = client.head_object(
                Bucket=bucket,
                Key=digest_key,
                ChecksumMode="ENABLED",
            )
        except (BotoCoreError, ClientError) as exc:
            raise CommandError("PostgreSQL backup remote verification failed.") from exc

        objects = (
            (archive_key, archive_path, archive_head),
            (digest_key, digest_path, digest_head),
        )
        receipt_objects = []
        for key, path, head in objects:
            metadata = {
                str(name).lower(): value
                for name, value in head.get("Metadata", {}).items()
            }
            version_id = head.get("VersionId")
            object_digest = self._sha256(path)
            object_checksum = base64.b64encode(
                bytes.fromhex(object_digest)
            ).decode("ascii")
            if (
                head.get("ContentLength") != path.stat().st_size
                or metadata.get("sha256") != digest
                or head.get("ChecksumSHA256") != object_checksum
                or version_id in {None, "null"}
            ):
                raise CommandError("PostgreSQL backup remote verification failed.")
            receipt_objects.append(
                {
                    "key": key,
                    "version_id": version_id,
                    "content_length": head["ContentLength"],
                    "sha256": object_digest,
                }
            )
        return {
            "completed_at": timezone.now().isoformat(),
            "bucket": bucket,
            "archive_sha256": digest,
            "objects": receipt_objects,
        }

    def _write_remote_success_receipt(self, output_dir, receipt):
        """Atomically publish success only after both remote objects verify."""

        receipt_path = output_dir / REMOTE_SUCCESS_RECEIPT
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output_dir,
                prefix=".remote-backup-receipt-",
                delete=False,
            ) as temporary:
                temporary.write(json.dumps(receipt, sort_keys=True) + "\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, receipt_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _prune_old_backups(self, output_dir, retain_days, max_local_archives):
        cutoff = timezone.now() - timedelta(days=retain_days)
        for backup_path in output_dir.glob(f"{BACKUP_PREFIX}-*"):
            if backup_path.suffix not in {".dump", ".sha256"}:
                continue
            modified_at = datetime.fromtimestamp(
                backup_path.stat().st_mtime,
                tz=timezone.get_current_timezone(),
            )
            if modified_at < cutoff:
                backup_path.unlink()

        archives = sorted(
            output_dir.glob(f"{BACKUP_PREFIX}-*.dump"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for archive_path in archives[max_local_archives:]:
            archive_path.unlink(missing_ok=True)
            archive_path.with_suffix(".dump.sha256").unlink(missing_ok=True)
