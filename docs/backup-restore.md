# Backup And Restore Runbook

Production recovery uses two independent layers:

- a PostgreSQL custom-format archive and SHA-256 sidecar in private,
  versioned, KMS-encrypted S3; and
- a DLM policy scheduled to create daily encrypted snapshots of the 32 GB EC2
  data volume and retain 30 recovery points.

A backup is not accepted until its checksum matches, `pg_restore --list`
succeeds, and a restore into an isolated empty database or cloned volume has
been tested.

Production now runs on the EC2/Caddy/PostgreSQL stack. The former RDS instance,
ECS web service, and ALB have been removed. Changing a legacy Terraform
variable is therefore not a rollback: recovery requires an isolated
rebuild/restore, reconciliation, approval, and a separate traffic cutover.

## Create and verify the production backup

The systemd backup timer runs daily. An operator can run the same workflow
through Session Manager:

```sh
sudo /usr/local/sbin/duducar-command backup
```

The command dumps as the non-superuser application role, validates the archive
catalogue, writes a SHA-256 sidecar, uploads both objects beneath
`database-backups/`, and prunes local files older than 30 days. It cannot
delete S3 backups.

Record the archive and sidecar object keys, version IDs, timestamps, KMS key,
and checksum. Download those exact object versions to a restricted scratch
directory before recovery:

```sh
sha256sum --check duducar-signage-postgres-YYYYMMDDTHHMMSSZ.dump.sha256
pg_restore --list duducar-signage-postgres-YYYYMMDDTHHMMSSZ.dump >/dev/null
```

The S3 lifecycle expires current and noncurrent database-backup versions after
30 days. Bucket versioning is a recovery layer, not permission to omit the
exact version used for a restore.

## Logical restore order

Never restore over a populated destination and never run migrations before
restoring the archive.

1. Prove the destination account, Region, instance, data volume, literal
   `signage` database, and exact archive version. Confirm that production DNS
   and the production Elastic IP do not point at this recovery host. Stop
   Django, Caddy, timers, and media dispatch for the destination, and confirm
   no target media task remains:

   ```sh
   sudo systemctl stop \
     duducar-health.timer \
     duducar-playlists.timer \
     duducar-media-reconcile.timer \
     duducar-retention.timer \
     duducar-backup.timer
   sudo systemctl stop duducar.service
   sudo docker start duducar-postgres
   ```

2. Verify the exact sidecar and archive catalogue before changing the
   destination:

   ```sh
   cd /srv/duducar/backups
   sha256sum --check --strict \
     duducar-signage-postgres-YYYYMMDDTHHMMSSZ.dump.sha256

   sudo -i
   source /etc/duducar/release.env
   docker run --rm \
     --read-only \
     --cap-drop ALL \
     --security-opt no-new-privileges:true \
     --volume /srv/duducar/backups:/backups:ro \
     --entrypoint pg_restore \
     "$BACKEND_IMAGE" \
     --list \
     /backups/duducar-signage-postgres-YYYYMMDDTHHMMSSZ.dump \
     >/dev/null
   exit
   ```

3. Record the destination's pre-restore aggregate counts. Only after proving
   that this exact destination is disposable, recreate the literal `signage`
   database. Preserve the locally generated cluster roles and credentials:

   ```sh
   sudo docker exec --user postgres duducar-postgres \
     psql -X --set ON_ERROR_STOP=1 \
     --username duducar_admin --dbname postgres \
     --command "DROP DATABASE signage WITH (FORCE)"

   sudo docker exec --user postgres duducar-postgres \
     psql -X --set ON_ERROR_STOP=1 \
     --username duducar_admin --dbname postgres \
     --command "CREATE DATABASE signage OWNER signage_owner TEMPLATE template0" \
     --command "REVOKE ALL ON DATABASE signage FROM PUBLIC" \
     --command "GRANT CONNECT ON DATABASE signage TO signage_owner, signage_app"
   ```

4. Apply the reviewed schema ownership and default runtime grants before
   restored objects are created:

   ```sh
   sudo /usr/local/sbin/duducar-command grant-runtime signage
   ```

5. Restore as the non-superuser `signage_owner`. The archive is mounted
   read-only and the owner password is read only from its root-controlled
   runtime file:

   ```sh
   sudo -i
   source /etc/duducar/release.env
   docker run --rm \
     --network duducar \
     --read-only \
     --cap-drop ALL \
     --security-opt no-new-privileges:true \
     --pids-limit 256 \
     --memory 768m \
     --cpus 1.5 \
     --env PGHOST=duducar-postgres \
     --env PGPORT=5432 \
     --env PGDATABASE=signage \
     --env PGUSER=signage_owner \
     --env PGSSLMODE=require \
     --env RESTORE_ARCHIVE=/backups/duducar-signage-postgres-YYYYMMDDTHHMMSSZ.dump \
     --volume /srv/duducar/backups:/backups:ro \
     --volume /run/duducar/database-owner:/run/duducar/database-owner:ro \
     --entrypoint /bin/sh \
     "$BACKEND_IMAGE" \
     -ec 'export PGPASSWORD="$(cat /run/duducar/database-owner/database-owner-password)"; exec pg_restore --exit-on-error --single-transaction --no-owner --no-privileges --dbname="$PGDATABASE" "$RESTORE_ARCHIVE"'
   exit
   ```

   Do not add `--clean`, `--create`, or `--disable-triggers`.

6. Restore first, then migrate as `signage_owner`. Reapply runtime grants after
   each operation that can create database objects:

   ```sh
   sudo /usr/local/sbin/duducar-command grant-runtime signage
   sudo /usr/local/sbin/duducar-command migrate signage
   sudo /usr/local/sbin/duducar-command grant-runtime signage
   sudo /usr/local/sbin/duducar-command migration-check signage
   ```

7. While Django and Caddy remain stopped, use a short-lived application
   container on the private Docker network with the normal runtime environment
   and secret mounts. Confirm it connects as `signage_app`, then compare users,
   devices, assignments, immutable playlist versions, media metadata, playback
   evidence, audit events, representative reports, and
   `django_migrations`. Do not publish a port from this validation container.
8. Start Django and Caddy only after restore, grants, migrations, migration
   check, and application-role reconciliation have passed. Keep timers
   disabled until the restore is accepted:

   ```sh
   sudo systemctl start duducar.service
   sudo /usr/local/sbin/duducar-command readiness
   ```

9. Verify private media through the matching S3 object versions. PostgreSQL
   archives do not contain media binaries.
10. Create a fresh backup from the restored database and record the source
    recovery point, elapsed time, resulting RPO/RTO, and approver before
    enabling timers or moving traffic.

Do not mount the backup directory permanently into PostgreSQL. Use a
short-lived restore container with the archive mounted read-only and the
schema-owner credential supplied through its root-controlled file.

## Data-volume snapshot restore

The DLM policy is configured to create one snapshot every 24 hours and retain
30. Before relying on this path, verify an actual completed snapshot, its
source volume, timestamp, tags, encryption, and retention state. An enabled
policy or snapshot-creation event alone is not recovery evidence.

At the 2026-07-28 handoff, the policy had not reached its first scheduled run
and no retained DLM-created snapshot existed. Temporary encrypted snapshot
`snap-0da33c455687b6128` was used for the isolated rehearsal and later removed;
it is historical evidence, not a current recovery source.

A separate encrypted 32 GB manual bootstrap snapshot was retained pending the
first DLM run, with an operator review date of 2026-07-29. The live review on
2026-07-30 confirmed that it still exists and is complete. It also confirmed
the first complete encrypted DLM-managed snapshot, less than 24 hours old, and
an enabled daily policy scheduled for 18:30 UTC with 30 recovery points.

The review decision is to retain the manual snapshot temporarily. The isolated
volume rehearsal proved the recovery procedure against the earlier rehearsal
snapshot, but this review did not create a volume from the exact DLM-managed
recovery point. After that exact restore test passes, remove only the manual
bootstrap snapshot through a reviewed cleanup. If no current DLM-managed
snapshot exists at a future check, rebuild the data volume and use the verified
logical-restore path instead of claiming volume-level recovery.

The root and data volumes have different recovery responsibilities:

- Rebuild the root volume from the reviewed Terraform/bootstrap source and
  pinned image digests. It supplies Amazon Linux, SSM, Docker configuration,
  `/etc/duducar` scripts and systemd units, and `release.env`.
- A verified DLM snapshot of the 32 GB data volume supplies PostgreSQL data,
  the matching local `duducar_admin` and `signage_owner` credential files,
  Caddy state, Docker data, and local backup scratch space beneath
  `/srv/duducar`.
- Secrets Manager remains authoritative for Django, SMTP, Play Integrity,
  CloudFront signing, and the `signage_app` database password.

Use this isolated recovery procedure:

1. Record the exact snapshot ID, source-volume ID, creation time, tags,
   encryption key, filesystem type, and recovery point.
2. Create a separate encrypted GP3 volume from the snapshot in the recovery
   instance's Availability Zone. Never attach it over the live production
   volume or mount it on the production host for a rehearsal.
3. Build a separate recovery root instance from the current reviewed source.
   Permit Session Manager access only; do not associate the production Elastic
   IP, publish application ports, start timers, or change public DNS.
4. Before starting Docker, attach the cloned volume. Resolve its exact device
   with `lsblk -f` and `blkid`, verify that it is XFS, and first inspect it
   read-only with XFS `nouuid,norecovery`. Abort rather than formatting any
   device that already contains a filesystem.
5. Unmount the inspection mount, configure the recovery host's `/etc/fstab`
   for the exact restored device/UUID as appropriate, and mount it at
   `/srv/duducar`. Confirm the restored
   `/srv/duducar/postgres-secrets` files retain restrictive ownership and
   modes. They must match the role hashes in the restored PostgreSQL data; do
   not regenerate or replace them.
6. Install the reviewed `release.env`, render runtime files, and start the
   restored stack while the host remains isolated:

   ```sh
   sudo /usr/local/sbin/render-duducar-runtime-env
   sudo systemctl start docker
   sudo /usr/local/sbin/duducar-stack start
   sudo /usr/local/sbin/duducar-stack status
   sudo /usr/local/sbin/duducar-command readiness
   ```

7. Inspect PostgreSQL and system journals for WAL-recovery or filesystem
   errors. Connect through the application image as `signage_app`, compare all
   required aggregates and migrations, verify private media, and create a
   fresh logical backup.
8. Record measured RPO/RTO and approval. Promoting the recovered host requires
   a separate reviewed cutover after current production writes are quiesced
   and reconciled. Preserve the former production volume and latest logical
   backup through the rollback window.
9. For a rehearsal, stop the probe, unmount and detach the clone, and delete
   only the explicitly tagged temporary instance and volume. Do not manually
   delete the retained DLM source snapshot outside its lifecycle policy.

### Historical data-volume rehearsal evidence — 2026-07-28

The rehearsal passed PostgreSQL 16 startup, application-role access,
26 migrations, and aggregate counts of
`1|0|0|3|10|59|0|0` for users, devices, drivers, media, playlists, audit
events, playback batches, and playback events.

## Completed logical restore evidence — 2026-07-28

The accepted logical recovery used these exact versioned S3 objects:

- archive:
  `database-backups/duducar-signage-postgres-20260728T031801Z.dump`,
  version `1Fs9W.PSWR.sdNvg9cwF4RAYtXEuj7gc`;
- sidecar:
  `database-backups/duducar-signage-postgres-20260728T031801Z.dump.sha256`,
  version `csci5b1uRTrKhYeUV46iwK76RPmwzSSt`.

The checksum and archive catalogue passed. The archive was restored into an
empty PostgreSQL database as `signage_owner`; runtime grants, migrations,
migration checks, and application-role reads passed. `signage_app` retained
DML and `pg_dump` access but was denied DDL. The restored aggregate counts
matched the source values recorded above. Both the logical restore and
data-volume rehearsal passed the pilot's maximum 24-hour RPO and RTO gates.
Future drills must also retain exact start/end timestamps and temporary-resource
cleanup evidence in the operations record.

## Historical legacy RDS snapshot retained temporarily

The live RDS instance was removed after cutover. One encrypted final snapshot
is retained:

```text
duducar-signage-production-final-20260728t031513z
```

It is an encrypted 20 GB manual snapshot with `Retention=30-days` and
`ReviewAfter=2026-08-27`. This snapshot contains the frozen legacy database,
not writes accepted by EC2 after cutover.

Restoring it requires a new isolated RDS instance, security groups, credentials,
readiness checks, and reconciliation of all post-snapshot writes. Changing
Terraform booleans is not a rollback.

The review tag does not delete a manual RDS snapshot, and neither the S3
lifecycle nor DLM retention policy applies to it. Keep the project KMS key
enabled while this snapshot exists. On or after the review date, delete it only
after a reviewed operator confirms that current EC2 logical backups, DLM
snapshots, matching private-media versions, and restore evidence remain
healthy.

## Pilot fixture archive

The fixture command remains useful for local rehearsal, but it is not the
authoritative production backup:

```sh
python manage.py create_pilot_backup --output-dir /secure/signage-backups
python manage.py verify_pilot_backup \
  /secure/signage-backups/duducar-signage-YYYYMMDDTHHMMSSZ.tar.gz
```

Keep all archives outside the web root, restrict permissions, and never include
credentials, private keys, recovery codes, environment files, or raw media
URLs.
