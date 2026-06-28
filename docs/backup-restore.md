# Backup And Restore Runbook

Pilot backups must exclude secrets and include Django data plus local media
files. Production should still use encrypted database snapshots and private
object-storage backup/versioning, but these commands provide an application
level backup that can be tested before the pilot.

Create a backup:

```sh
python manage.py create_pilot_backup --output-dir /secure/signage-backups
```

Verify a backup archive:

```sh
python manage.py verify_pilot_backup /secure/signage-backups/duducar-signage-YYYYMMDDTHHMMSSZ.tar.gz
```

The command keeps archives for `PILOT_BACKUP_RETENTION_DAYS`, defaulting to 30
days. Store backup archives outside the web root, restrict file permissions, and
do not put production secrets or `.env` files in the backup directory.

For a disaster restore, restore the managed PostgreSQL snapshot and private
media bucket first, then use `verify_pilot_backup` to confirm the application
backup from the same recovery point is readable. Test this process before the
pilot launch and repeat after deployment changes.
