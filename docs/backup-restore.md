# Backup, Restore, and Isolated Recovery

Production recovery is a private, versioned, KMS-encrypted PostgreSQL archive
with SHA-256 sidecar plus daily encrypted DLM snapshots of the 32-GiB data
volume (30 recovery points). Accept a recovery point only after sidecar,
`pg_restore --list`, exact-media, and isolated restore evidence pass. The EC2
stack is current; legacy RDS/ECS/ALB are not rollback paths. RPO/RTO target is
24 hours. Use `infrastructure/recovery-smoke/README.md` and its helpers; this
page is the required control sequence.

## Backup and logical restore

- Daily backup is `sudo /usr/local/sbin/duducar-command backup`. Record exact
  archive/sidecar keys, versions, timestamps, KMS key, checksum and catalogue.
  S3 versioning is a recovery layer, not permission to omit the selected version.
- Before restore, identify a disposable destination, account/Region/host,
  literal `signage` database, archive and sidecar versions; prove production DNS
  and EIP never target it. Stop its stack/timers/media dispatch; never restore
  over a populated database or migrate before restore.
- Verify sidecar and catalogue before changing data. Recreate only the
  disposable `signage` database; preserve generated roles/credentials. Restore
  as non-superuser `signage_owner` from a read-only archive mount—never use
  `--clean`, `--create`, or `--disable-triggers`.
- Grant runtime permissions, restore, migrate, grant again, and run migration
  check. While public services remain stopped, verify `signage_app` access,
  aggregates/migrations, immutable records, private-media object versions and
  report data. Start only after all pass; then make a fresh backup and record
  source, elapsed RPO/RTO, reconciliation and approver.
- Archives never contain media binaries. Keep scratch restricted; do not mount
  backups permanently into PostgreSQL/expose ports; lifecycle deletion is a
  separate explicit release decision.

## Snapshot clone rules

- A completed DLM snapshot must match the recorded source data volume, tags,
  encryption and retention; a scheduled policy or event is not proof. Rebuild
  the root from reviewed source and use the clone only on an isolated recovery
  host—never attach it to production, EIP, DNS, inbound rules or public app port.
- Root contains runtime/bootstrap; cloned data contains PostgreSQL, local
  credential files and Docker state. Secrets Manager remains authoritative for
  application secrets. Do not regenerate clone database credentials.
- The sole allowed filesystem interface is `duducar-recovery-mount`. Run
  `inspect`; clean result permits `mount` then `verify-mounted`. Never direct
  mount, use fstab, format, generic repair or `xfs_repair -L`.
- Only an exit-3 recognized dirty journal permits the exact clone-only action
  `replay-journal --confirm "REPLAY-JOURNAL <operation-id>"`; review its
  root-only diagnostic first. Any other failure or failed replay stops the
  drill and preserves diagnostics—no retry/manual workaround.

## Isolated application recovery drill

### Authorize and preflight

1. Obtain written approval naming account/Region, operator, cost limit,
   32-hex fresh operation ID, exact DLM snapshot/source volume, archive/sidecar
   and media versions, reviewed commit/digests, owner, approver and cleanup time.
   Never reuse an operation ID, state, variables file or destroyed drill.
2. Start from the reviewed release commit. Prove `dlm:managed=true` and
   `DLMBackup=duducar-signage-production-ec2-target-data`, current archive
   checksum/catalogue, selected normalized media version/hash/size and <24h
   recovery age. Do not create production backups or proof data for a drill.
3. Keep an out-of-repository 0600 tfvars file. Use only
   `infrastructure/recovery-smoke/recovery-terraform`, which owns the isolated
   state `recovery-smoke/<operation-id>.tfstate`; never raw Terraform, a
   production backend/state, saved-plan apply, SSH or ingress.
4. Review the wrapper plan: only tagged recovery instance/clone/security group/
   role/profile; zero inbound, no EIP/DNS/route/production resource change. Its
   role may read exact approved S3/ECR/secret objects and SSM only—no production
   writes, IAM, database, snapshot, ECS, SNS, CloudFront or Secrets mutations.
5. Apply only after reviewing that plan and typing its exact `APPLY <operation-id>`
   confirmation. An apply never authorizes cutover, DNS, source deletion or
   production writes.

### Restore, smoke, and evidence

1. Use SSM only. Keep Docker/timers quarantined and recovery-specific; never
   start cloned Docker state, normal stack/release.env, production Caddy/timers,
   backup, media, email or alert actions. Mount by the helper sequence above.
2. For the snapshot path, run `snapshot-schema` and `media`, start only the
   recovery stack, and require status/TLS and application smoke checks. Then
   stop it, run `logical` and `media`, restart, and repeat the smoke against the
   logical restore. The media step must validate the exact downloaded private
   object version against preflight and restored metadata.
3. Retrieve only the public recovery CA by SSM, fingerprint-match `tls-info`,
   then run the exact output SSM port forward. Use a temporary browser profile
   and temporary hostname mapping/NSS trust; remove after. Never system-trust,
   alter `/etc/hosts`/DNS, use production hostname, `-k`, click-through or HTTP.
4. Verify live/ready, owner login, protected dashboard/report and one unfiltered
   playback CSV, then logout/protected redirect. A header-only CSV proves the
   export path only if the chosen source genuinely has zero playback events;
   never fabricate/seed evidence. Do not retain CSV values or change clone data.
5. Record redacted source/state/op IDs, host/volume IDs, timings/RPO/RTO, TLS/
   loopback results, owner/report/CSV result, aggregate/media comparison and cost.

### Cleanup

Stop stack, close tunnel, unmount/detach clone, then only type exact
`DESTROY <operation-id>` for the wrapper. Run its `cleanup-check`; retain state
and redacted evidence. Confirm no tagged recovery resources remain and that
production instance, volume, snapshot, archives and media versions are unchanged.

## Live rollback boundary

Use reviewed forward fixes and backward-compatible migrations. Before migration,
a same-input release-config rollback may restore its saved selection. After
`0010_battery_backed_player_policy`, never deploy a pre-policy image or reverse
the migration live. A data restore or cutover needs separate approval,
reconciliation, and acceptance of possible data loss.
