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
`database-backups/`, and keeps a short local cache of at most three archives
for no more than three days. The S3 lifecycle remains the authoritative 30-day
retention layer; the command cannot delete S3 backups.

Record the archive and sidecar object keys, version IDs, timestamps, KMS key,
and checksum. Download those exact object versions to a restricted scratch
directory before recovery:

```sh
sha256sum --check duducar-signage-postgres-YYYYMMDDTHHMMSSZ.dump.sha256
pg_restore --list duducar-signage-postgres-YYYYMMDDTHHMMSSZ.dump >/dev/null
```

After the reviewed Terraform lifecycle change is applied, the S3 lifecycle
expires each unique current database-backup object after 30 days. Because
expiration in a versioned bucket first makes the payload a noncurrent version,
that payload is then removed after one additional lifecycle day; it is not
retained for a second 30-day period. Until that apply, the live bucket retains
noncurrent backup versions for 30 days. Treat the change to one day as an
explicit retention/deletion decision and record it in the release approval.
Bucket versioning is a recovery layer, not permission to omit the exact version
used for a restore.

### Approved retention decision — 2026-08-08

The project owner approved changing noncurrent database-backup versions from 30
days to one day for this release. The live rule remains `retain-30-days` with
30 current and 30 noncurrent days until the final image-pinned Terraform plan
is reviewed and explicitly applied. Do not treat this approval as authorization
to apply unrelated infrastructure changes or delete the manual bootstrap
snapshot.

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
schema-owner credential supplied through its root-controlled file. Keep the
scratch directory non-world-readable, but make it traversable and the selected
archive readable by the restore container's exact UID/GID. When a temporary
least-privilege role reads an SSE-KMS bucket with S3 Bucket Keys enabled, bind
`kms:Decrypt` to the exact bucket-ARN encryption context while retaining exact
object ARNs in the S3 statement.

## Data-volume snapshot restore

The DLM policy is configured to create one snapshot every 24 hours and retain
30. Before relying on this path, verify an actual completed snapshot, its
source volume, timestamp, tags, encryption, and retention state. An enabled
policy or snapshot-creation event alone is not recovery evidence.

At the 2026-07-28 handoff, the policy had not reached its first scheduled run
and no retained DLM-created snapshot existed. Encrypted 32 GB manual bootstrap
snapshot `snap-0da33c455687b6128` was created and used for the first isolated
rehearsal, then retained pending an exact DLM-managed restore. The 2026-08-01
review confirmed that this manual snapshot still exists; it was not deleted as
an earlier version of this runbook incorrectly stated.

The exact DLM-managed restore passed on 2026-08-01. The manual bootstrap
snapshot is therefore no longer required as the primary recovery bridge, but
it remains until a separately reviewed deletion explicitly names that snapshot.
Do not confuse it with a DLM-managed recovery point. If no current DLM-managed
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

## Completed isolated restore rehearsal — 2026-08-01

The rehearsal ran in account `173454940059`, Region `ap-southeast-5`, without
DNS, Elastic IP, inbound security-group rules, public application ports, or any
attachment to the production host. It used these exact recovery points:

- DLM snapshot `snap-0c88946782a855f6a`, created from production data volume
  `vol-05b6edc95de87cc4a` at `2026-07-31T18:51:04.832Z`;
- archive
  `database-backups/duducar-signage-postgres-20260731T180215Z.dump`, version
  `lHZ4gSz9dyoj.LLWrC2Wyn8nmPqMY2Lc`, SHA-256
  `dce279c5a5a659495583fe9126bb87d2d8335829c3f6daa47ac2dff37e3843cf`;
  and
- sidecar
  `database-backups/duducar-signage-postgres-20260731T180215Z.dump.sha256`,
  version `6za9xE.afr9tzVV2gegTFb0E0JaIw04m`.

The snapshot clone first passed an XFS read-only `nouuid,norecovery` inspection,
including PostgreSQL 16 data and restrictive local database-secret modes. On
the writable clone, digest-pinned PostgreSQL 16.14 completed automatic WAL
recovery, the runtime role could read but could not create schema objects, and
`xfs_repair -n` passed after clean shutdown. The versioned logical archive
passed its sidecar and catalogue checks, restored into an empty database as
non-superuser `signage_owner`, left all public data objects owned by that role,
and applied the expected `signage_app` DML/default privileges while denying
DDL and `TRUNCATE`.

Both paths matched the live aggregate counts
`1|0|0|3|10|63|0|0|26` for users, devices, drivers, media, playlists, audit
events, playback batches, playback events, and migrations. All three
database-referenced normalized media objects matched their exact current S3
versions, byte sizes, and SHA-256 values. At the `2026-08-01T11:22:37Z`
baseline, the logical and snapshot recovery points were approximately 17 hours
20 minutes and 16 hours 32 minutes old, respectively, within the 24-hour RPO.

The temporary instance launched at `11:27:16Z`; the snapshot database was
ready at `11:33:30Z`, the logical restore completed at `11:37:37Z`, media
verification completed at `11:40:17Z`, and termination began at `11:42:01Z`.
The two database paths therefore completed within 10 minutes 21 seconds of
instance launch, and the full rehearsal plus cleanup took 14 minutes 45
seconds, within the 24-hour RTO. Retained SSM evidence is command
`58dc7ab0-e66f-4407-a0fa-21ba6325a755` for the snapshot restore,
`6243287d-7f8d-4da4-9550-e995ecbf7419` for the logical restore, and
`6d25da20-3e0c-40c4-aa97-8dc22345812d` for private-media verification.
At the current Malaysia catalogue rates, the temporary instance, 40 GB of
short-lived GP3 storage, ephemeral public IPv4 address, and request charges
are estimated to total less than USD 0.01.

Temporary instance `i-0e92e34286d48d16a`, clone
`vol-0c2aeba93de1ed469`, its root volume, security group, IAM role, and instance
profile were removed. Post-cleanup checks found no resources with operation ID
`de909555b52a`; production instance `i-0f814d6d80f175319` remained running with
its original root and data volumes, and the DLM source snapshot remained
complete. This drill did not start Django or Caddy or consume production
application secrets, so a restored-dashboard login and representative-report
smoke test remains a separate application-level recovery gate.

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
