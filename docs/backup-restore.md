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

The battery-backed policy migration is forward-only for live service. Its
legacy qualification columns provide historic/read compatibility only; they do
not restore the former external-power policy or make a pre-policy application
image a normal production rollback. Once `0010_battery_backed_player_policy`
is recorded, the safe live code path is the released image or a reviewed
forward fix. A pre-policy image may be used only for read-only investigation on
an isolated recovered data set. Returning live production to a pre-migration
backup is a separately approved data-recovery decision with reconciliation and
possible data loss, not a routine application rollback.

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

The preceding data-volume steps describe a separately approved disaster
recovery rebuild. They do **not** apply to the disposable application-layer
smoke below. For that smoke, the recovery root supersedes every use of the
normal production `/etc/duducar/release.env`,
`render-duducar-runtime-env`, `duducar-stack`, `duducar.service`, production
timers, and `duducar-command backup`. Use only the recovery-specific runtime,
stack, loopback listener, and read-only production permissions; never create a
fresh production backup from the cloned data volume.

## Application-layer recovery smoke — isolated Terraform root

The database and media checks above do not prove that a restored Django/Caddy
application can serve an owner. Run this additional smoke test before relying
on the recovery procedure for release approval. It is a disposable,
application-layer test only: it must never become a standby host, receive
production traffic, or write to a production data store.

Use the separate `infrastructure/recovery-smoke` Terraform root. It has one
remote state object per drill:

```text
recovery-smoke/<operation-id>.tfstate
```

This is intentionally not a Terraform workspace in the production root and
must never use `production/terraform.tfstate`. The state object, its lock, and
all temporary resources are tied to the same operation ID. Keep the empty
post-destroy state object as change evidence unless a separately approved
retention procedure removes it.

The recovery root receives all source identifiers explicitly rather than
reading or modifying production Terraform state. Its required inputs include
the 32-hex-character `operation_id`, DLM `source_snapshot_id` and
`source_data_volume_id`, exact logical archive and sidecar S3 keys and version
IDs, exact normalized-media S3 key and version ID, that media record's
preflight SHA-256 and byte size, recovery subnet/VPC CIDR, data and application
KMS key ARNs, application secret ARN, digest-pinned backend/PostgreSQL/Caddy
images, and the reviewed ARM64 AMI ID. Values may identify production resources
but must not include secret values. Store the temporary variables file outside
the repository with mode `0600`.

### Preflight

1. Obtain a written maintenance/recovery-test approval that names the account,
   Region, operator, cost limit, exact source snapshot, data volume, archive
   and sidecar object versions, reviewed Git commit, image digests, and
   operation ID. Generate the ID once and never reuse it for another drill:

   ```sh
   recovery_operation_id=$(openssl rand -hex 16)
   test "${#recovery_operation_id}" -eq 32
   printf '%s\n' "$recovery_operation_id"
   ```

2. Start from the reviewed release commit and verify both the caller and
   source recovery points. Confirm that the snapshot belongs to the stated
   production data volume and has both `dlm:managed=true` and
   `DLMBackup=duducar-signage-production-ec2-target-data`; this excludes the
   retained manual bootstrap snapshot. Confirm the archive sidecar and
   catalogue pass, and record one ready `MediaAsset` normalized key, SHA-256,
   size, and exact S3 version. Do not create a new production backup as part of
   this smoke test.

3. Create a protected, out-of-repository variables file for that one operation
   and initialize only the recovery root through its mandatory
   `recovery-terraform` wrapper. It fixes and verifies the isolated backend;
   never pass `../terraform/backend.hcl` or invoke a raw stateful Terraform
   command in this root:

   ```sh
   recovery_tf=./infrastructure/recovery-smoke/recovery-terraform
   "$recovery_tf" init --operation-id "$recovery_operation_id"
   "$recovery_tf" fmt --operation-id "$recovery_operation_id" -check
   "$recovery_tf" validate --operation-id "$recovery_operation_id"
   "$recovery_tf" plan --operation-id "$recovery_operation_id" \
     -var-file=/secure/duducar-recovery/${recovery_operation_id}.tfvars \
     -out=/secure/duducar-recovery/${recovery_operation_id}.tfplan
   ```

   The `operation_id` in the variables file must exactly equal
   `recovery_operation_id`. Before any apply, inspect the saved plan and the
   planned backend key. The only permitted production reads are validation of
   the named source volume/snapshot and ECR digest, followed at runtime by the
   exact selected archive, sidecar, and normalized-media S3 object versions.
   Abort if the plan proposes a change to the production instance, Elastic IP,
   Route 53 records, production security group, live data volume, source
   snapshot, existing IAM role, backup objects, or the production Terraform
   state path.

4. Confirm the plan creates only resources carrying
   `OperationId=<operation-id>` and the recovery-specific role/profile,
   security group, instance, and cloned volume. There must be no inbound
   security-group rule, Elastic IP, public application listener, DNS record,
   or route change. The instance has an ordinary ephemeral public IPv4 only for
   outbound HTTPS because this public subnet has no NAT or interface endpoints;
   it is not an administrative or application path. Administrative access is
   through Systems Manager only; SSH is not an exception.

5. Review the temporary instance role before applying. It may read only the
   selected versioned archive, sidecar, and normalized-media object, approved
   application secret, fixed backend ECR repository image, and SSM channels.
   It must have no production write capability: in particular no S3
   `PutObject`, delete, lifecycle, or list permission on production buckets;
   no snapshot, volume, DNS, IAM, Secrets Manager, database, ECS, SNS, or
   CloudFront mutation permission. The recovery application must not be given
   the production instance profile or credentials.

6. Explicitly approve the reviewed saved-plan **evidence**, then make a fresh,
   verified-backend apply using the identical variables file. Do **not** pass
   any saved plan path to `apply`: a Terraform plan embeds backend information.
   The wrapper creates a new private plan after checking its recovery backend
   and default workspace, displays it, and asks its own literal
   `APPLY <operation-id>` confirmation. Record the root outputs
   `recovery_instance_id`, `recovery_volume_id`,
   `recovery_security_group_id`, `recovery_instance_profile_name`, and
   `recovery_operation_id` before connecting. An apply does not authorize a
   production cutover, a DNS change, or removal of the source snapshot. Bind
   the approval to the operation ID:

   ```sh
   "$recovery_tf" apply --operation-id "$recovery_operation_id" \
     -var-file=/secure/duducar-recovery/${recovery_operation_id}.tfvars
   "$recovery_tf" output --operation-id "$recovery_operation_id"
   ```

### Restore and smoke-test procedure

1. Connect only through the output `recovery_ssm_port_forward_command` and a
   Session Manager shell. Verify the recovery security group has zero ingress
   rules and, if the host has its expected ephemeral public IPv4, that it is
   used solely for outbound HTTPS. Keep the SSM port-forward session local to
   the reviewing operator.

2. Run only `duducar-recovery-mount inspect` and then
   `duducar-recovery-mount mount`; do not mount the device directly. `mount`
   fails closed unless the recovery-only root-volume receipt proves a successful
   read-only XFS `nouuid,norecovery` layout inspection followed by an unmounted
   `xfs_repair -n`, tied to this operation, clone device, source snapshot, and
   source volume. The renderer, restore helper, and stack revalidate that exact
   mounted clone and receipt before they read a secret or start Docker. Never
   attach or mount the source volume on the production host.

3. **Quarantine the cloned Docker state before starting Docker.** The data
   volume contains `/srv/duducar/docker`, which is the production Docker
   data-root. Its copied container metadata and `unless-stopped` restart
   policies can restart cloned PostgreSQL, Django, or Caddy as soon as a daemon
   points at it. The recovery bootstrap must instead use its recovery-only,
   root-volume Docker data-root. The recovery helper validates the exact
   root-owned daemon JSON and absence of local systemd Docker replacements or
   drop-ins before unmasking it, then rechecks `docker info`; do not bypass it.
   Do not start Docker against the clone, run the normal `duducar-stack`
   helper, reuse the production `release.env`, or use the production Caddy
   configuration.

4. Keep every `duducar-*` timer disabled or masked, including backup, health,
   playlist, media-reconcile, and retention timers. Do not invoke
   `duducar-command backup`, `create_postgres_backup`, media dispatch, email
   delivery, or alert delivery. The temporary role is deliberately read-only
   to production, but disabled timers and a recovery-specific runtime
   configuration are required defence in depth. Dashboard login/session and
   audit rows written to the cloned database are local to the drill.

5. Start only the recovery root's generated recovery stack. Its Caddy
   configuration must use recovery-only TLS and bind its published listener to
   the recovery host loopback interface. It must not use production DNS,
   production TLS private keys, the production `Caddyfile.post-cutover`, or an
   internet-routable bind. Check the listening socket on the recovery host
   before opening the tunnel; an external scan is not a substitute for this
   check. Run `duducar-recovery-restore snapshot-schema` (or `logical`) and
   then `duducar-recovery-restore media` before the web stack: the latter
   downloads the exact selected S3 object version and fails unless it matches
   both the recorded preflight SHA-256/size and the restored
   `MediaAsset.normalized_file`, SHA-256, and file size.

6. Run the exact `recovery_ssm_port_forward_command` output locally, then use
   the reserved operation-specific `recovery_tls_hostname` (for example,
   `recovery-<operation-id>.duducar.test`) and `recovery_tls_port` through the
   loopback tunnel. Map only that reserved hostname to `127.0.0.1` in a
   temporary browser profile or use `curl --resolve`; import only the public
   recovery CA at `recovery_tls_ca_path`, verified with
   `duducar-recovery-stack tls-info`. Retrieve that public CA through the
   Session Manager shell only (for example, `sudo base64 -w0
   /run/duducar-recovery/caddy-data/caddy/pki/authorities/local/root.crt`),
   decode it into a temporary local file, compare its SHA-256 certificate
   fingerprint to `tls-info`, and remove it after the smoke. Do not use `-k` or
   a browser certificate click-through. Do not create a Route 53 record, use a
   production hostname, or bypass TLS with plain HTTP. Verify `/health/live/`
   and `/health/ready/`, sign in as an existing account owner, open one
   representative report containing restored data, export one representative
   playback CSV from the **clone**, and log out. Do not retain or copy CSV
   contents, upload media, publish a playlist, create an enrollment code, or
   change a user/device.

7. Record the source versions, state key, operation ID, image digests,
   recovery host/volume IDs, test start/end times, TLS and loopback evidence,
   HTTP results, owner-login/report result, restored aggregate comparison, and
   observed cost. Redact secrets, session cookies, access tokens, raw media
   URLs, and personal data from the evidence.

### Explicit cleanup

The recovery root never performs automatic cleanup: do not rely on a failed
test, session expiry, or Terraform state removal to terminate resources. Once
evidence is collected, stop the recovery stack, close the SSM tunnel, unmount
and detach the clone, and require an operator confirmation bound to the same
operation ID before destroying the root:

```sh
printf 'Type DESTROY %s to remove only this recovery drill: ' \
  "$recovery_operation_id"
read -r recovery_confirmation
test "$recovery_confirmation" = "DESTROY $recovery_operation_id"

"$recovery_tf" output --operation-id "$recovery_operation_id" -raw \
  recovery_cleanup_query
"$recovery_tf" destroy --operation-id "$recovery_operation_id" \
  -var-file=/secure/duducar-recovery/${recovery_operation_id}.tfvars
```

After destroy, run the wrapper's read-only cleanup check and retain its result
with the change record:

```sh
"$recovery_tf" cleanup-check --operation-id "$recovery_operation_id"
```

It first verifies that the configured profile resolves to guarded account
`173454940059`, then must report no temporary instance, cloned volume, recovery
security group, other taggable resource, or named recovery IAM role/profile.
Do not use a broad tag query, manually delete the source snapshot, or delete
archive/sidecar/media versions. Confirm the production instance and original
data volume remained unchanged. Keep the isolated state path and its
lock/history as audit evidence unless its deletion is separately authorized.

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
