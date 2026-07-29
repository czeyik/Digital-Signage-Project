# USD 30 Production Architecture And Historical Migration Record

> **Archived operational evidence:** This records the completed 2026-07-28
> migration and must not be used as a current deployment procedure. Use the
> [production runbook](../production-deployment-runbook.md),
> [backup and restore runbook](../backup-restore.md), and
> [infrastructure operator guide](../../infrastructure/README.md) for current
> operations.

## Execution status

The migration completed on 2026-07-28. Both production hostnames now resolve
to the EC2 Elastic IP; systemd manages PostgreSQL, Django, Caddy, and all five
maintenance timers. The legacy ECS services/schedules, ALB, and live RDS
instance have been removed. The ECS cluster remains only for isolated
on-demand media tasks.

The final gates passed:

- valid public certificates, HTTPS readiness, security headers, and
  HTTP-to-HTTPS redirect;
- private CloudFront media delivery with signed requests allowed and unsigned
  or invalid requests denied;
- exact logical restores and an EBS snapshot clone booted through PostgreSQL
  and Django;
- runtime schema DDL denied to `signage_app`;
- a bounded 400-request load test with no unexpected responses;
- automatic encrypted-volume/container/timer recovery after an EC2 reboot; and
- a post-cutover on-demand worker that exited zero against the EC2 database.

The final security audit found that the AWS-managed SNS key blocked CloudWatch
and EventBridge publishers. Production now reuses the existing project KMS
key: CloudWatch is restricted to the three exact EC2 alarm ARNs and
EventBridge assumes a dedicated role that can publish only to the operations
topic with that key. Controlled ALARM/OK transitions both published
successfully, and a deliberately failed isolated task produced one
EventBridge/SNS invocation with no failed invocation. The host-health timer
also probes both public HTTPS readiness routes every 30 minutes.

One encrypted final RDS snapshot is retained for review through 2026-08-27.
The older pre-migration snapshot was removed after the final snapshot became
available. The project-filtered USD 30 budget is active, but AWS tag/cost data
is delayed and must be checked after 48 hours and through the first full month.

## Current operating boundary

The production stack is intentionally pilot-sized:

- one Amazon Linux 2023 ARM64 `t4g.small` instance;
- an encrypted 8 GB GP3 root volume and encrypted 32 GB GP3 data volume;
- one stable Elastic IP, with no SSH ingress;
- Caddy, Django, and PostgreSQL containers managed by systemd;
- PostgreSQL exposed only to the existing ECS worker security group on port
  5432, while ports 80 and 443 are public;
- Session Manager access through an instance profile and IMDSv2;
- one isolated Fargate media task per upload, not a polling worker;
- private S3 media delivery through CloudFront OAC and signed URLs;
- daily logical S3 backups plus an enabled DLM policy scheduled to retain 30
  incremental data-volume snapshots; and
- native EC2 status, CPU, and low-credit alarms without paid detailed
  monitoring or Container Insights.

This shape is designed for the 10-device pilot, not the three-year 1,000-device
target. PostgreSQL and Django share one host, so production has a single-host
failure domain. The scale path remains stateless web processes, managed
PostgreSQL, and independently scaled workers after the pilot justifies the
cost.

The remaining phase-one and phase-two sections are historical implementation
evidence. Do not replay them against the current account: the temporary ACME
bridge, legacy ECS runtime, ALB, and RDS instance no longer exist. Current
recovery starts with the decision tree near the end of this document and the
backup runbook.

## Estimated steady-state cost

The expected target is roughly USD 25-28 per month including Malaysian service
tax, before unusual media egress or an extended Fargate processing burst. The
largest assumptions are one `t4g.small` for 730 hours, 40 GB total GP3, one
public IPv4 address, incremental snapshots, low S3/ECR storage, three standard
EC2 alarms, and CloudFront usage remaining inside the account/organization
allowance.

The USD 30 budget filters on `Project=duducar-signage`. AWS Billing must
have the `Project` user-defined cost-allocation tag activated before that
filter produces complete data. Tagged budgets can omit tax and charges AWS
cannot allocate to a resource, so keep the account-wide budget until the
project-filtered budget reports stable data and compare Cost Explorer totals
weekly.

### Historical overlap and snapshot note — 2026-07-28

The legacy and replacement stacks overlapped during phase one. That migration
month is not a valid steady-state USD 30 measurement.

At the handoff, the DLM policy was enabled but had not reached its first
scheduled production run. Temporary encrypted snapshot
`snap-0da33c455687b6128` was used for the isolated restore rehearsal and later
removed. It is not a current recovery source. A separate manual encrypted
bootstrap snapshot was retained pending the first DLM run with
`ReviewAfter=2026-07-29`. The live review on 2026-07-30 confirmed that this
complete encrypted 32 GB manual snapshot still exists. It also confirmed a
complete encrypted DLM-managed snapshot less than 24 hours old and the enabled
daily 18:30 UTC policy with a retention count of 30. Retain the manual snapshot
until a volume created from the exact DLM-managed recovery point passes the
isolated restore procedure; an enabled policy or completed snapshot alone is
not restore evidence.

## Terraform controls

### Historical staging defaults

The values below were the safe defaults for initially staging the replacement.
They are not current production values and must not be applied to the live
state:

The phase-one resources are disabled by default:

```hcl
enable_continuous_media_worker = false
enable_container_insights      = false
enable_media_cloudfront        = false
enable_ec2_target              = false
enable_ec2_acme_bridge         = true
ecs_web_desired_count          = 1
enable_ecs_schedules           = true
application_origin             = "alb"
migration_budget_usd           = 30
```

Terraform rejects `enable_ec2_target=true` unless private CloudFront media
delivery is also enabled, and rejects `application_origin="ec2"` unless the
target exists. The existing `aws_route53_record.application` resource address
is retained while its destination switches between the ALB alias and EIP, so a
reviewed cutover does not replace the record's Terraform identity.

Current production uses `application_origin="ec2"` with the EC2 target and
private CloudFront path enabled. The legacy ECS runtime, ALB, RDS, continuous
worker, legacy schedules, and ACME bridge are disabled. The reviewed
`infrastructure/terraform/production-controls.tfvars.example` records these
non-secret switches; merge it with the site-specific values, immutable image
digest, and CloudFront public key in the ignored live variables file. Every
proposed change still requires a saved-plan review.

Uploads dispatch only the isolated `-ec2-media-worker` task definition. It
receives the production EC2 private address and runtime database credential,
can reach PostgreSQL only through its dedicated worker security group, and
exits after processing the bounded upload queue. The EC2 instance role may run
only that task definition and may pass only its execution and task roles to
`ecs-tasks.amazonaws.com`. A production systemd reconciliation timer recovers
failed or expired dispatches. The obsolete RDS-backed worker definitions have
no service, schedule, or current invocation path.

## Current secret and signing-key handling

Never put the CloudFront private key or a database password in Terraform,
user-data, an SSM command body, Git, or shell history.

Generate a dedicated RSA key pair offline. Give the PEM public key to Terraform
through the untracked variables file. Enter the private key directly in the
existing application secret through the Secrets Manager console or another
approved secret-entry path.

The production application secret must contain:

- `DJANGO_SECRET_KEY`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON`
- `DB_PASSWORD` (the random password dedicated to the production
  `signage_app` PostgreSQL role)
- `AWS_CLOUDFRONT_PRIVATE_KEY` (the private half of the CloudFront signing key)

The bootstrap contains only secret ARNs and non-secret configuration. At each
service start, a root-owned script retrieves the secret with the instance
profile, validates required fields, writes short-lived files beneath
`/run/duducar`, and mounts them read-only into Django. Django receives only
`*_FILE` paths; every backend secret file is owned by UID 10001, mode `0400`,
and no secret value is placed in a Docker environment file. `DB_PASSWORD`
belongs only to the non-superuser `signage_app` runtime role used by Django and
the isolated media worker. Bootstrap generates separate random
`duducar_admin` and `signage_owner` credentials directly on the encrypted data
volume. The admin role has no network `pg_hba.conf` rule; the owner credential
is mounted only into a short-lived migration container and is never mounted
into the internet-facing web container. Secret values are not printed. Rotate
the runtime password only through a coordinated PostgreSQL-role and Secrets
Manager update.

## Historical: review and provision

1. Start from a clean, reviewed release commit and an authenticated production
   SSO session.
2. Run `terraform fmt`, `terraform validate`, and a saved plan. First codify
   `enable_continuous_media_worker=false` and
   `enable_container_insights=false`. Confirm that Terraform retains the ECS
   worker service at desired count zero and removes only the obsolete
   Container Insights task-count alarms.
3. Set `enable_media_cloudfront=true` and provide the PEM public key. Review
   the S3 bucket policy carefully: it grants CloudFront read access only to
   `validated/*`. The KMS key policy keeps the account-root IAM-enablement
   statement and adds decrypt/describe access only for the exact distribution.
4. Leave `enable_media_cloudfront=true` and set `enable_ec2_target=true`.
   Confirm the plan creates one ARM64 instance, one 32 GB data volume, one
   EIP/ENI, the target security group, instance and DLM roles, target worker
   task definition, DLM policy, three alarms, tagged budget, and temporary
   path-restricted ACME bridge. It must not change Route 53 application
   records, destroy RDS, or remove the ALB/ECS web service.
5. Apply only the reviewed saved plan. Wait for the instance to become managed
   by Session Manager and for
   `/var/lib/cloud/instance/duducar-bootstrap-complete` to exist.

Terraform ignores later AMI-parameter drift for this instance. Apply Amazon
Linux security updates through an audited maintenance window and replace the
instance only after a current data backup and restore rehearsal.

## Historical: host release preparation

Connect with Session Manager; port 22 is deliberately closed. Copy
`/etc/duducar/release.env.example` to `/etc/duducar/release.env`, make it
`root:root` mode `0600`, and replace every placeholder with an image reference
pinned by `@sha256:`. Keep the preflight Caddyfile selected:

```text
CADDY_CONFIG=/etc/duducar/Caddyfile.preflight
```

The bootstrap and service unit do not start the application automatically.
This prevents an empty candidate database or unreviewed image from becoming a
production service.

Render and inspect configuration without showing values:

```sh
sudo /usr/local/sbin/render-duducar-runtime-env
sudo find /run/duducar -maxdepth 1 -type f -printf '%f %m %u:%g\n'
```

Start or deploy the pinned stack:

```sh
sudo /usr/local/sbin/duducar-stack deploy
sudo /usr/local/sbin/duducar-stack status
```

The Docker data root, PostgreSQL data, Caddy state, and backup scratch space
live on the 32 GB volume. Journald is capped at 200 MB. The runtime containers
have memory, CPU, PID, capability, and no-new-privilege limits; Django also has
a read-only root filesystem.

Every application and scheduled-command systemd failure publishes through the
operations SNS topic. Alert state is persisted on the encrypted root volume,
so the same failure is sent only once until a successful run sends one recovery
message.
The 30-minute health timer also checks for less than 5 GB free and for a local
PostgreSQL backup older than 30 hours, using the same transition-based
deduplication. It additionally resolves and requests the public dashboard and
API HTTPS readiness URLs with normal certificate validation, catching DNS,
Caddy, TLS, or routing failure while the host itself is still running.

## Historical: database migration and rehearsal gate

Do not copy live data while writes continue and do not point the live ECS web
at candidate PostgreSQL.

1. Run and verify `create_postgres_backup` against RDS, upload its custom-format
   archive and SHA-256 sidecar to the private backup bucket, and record their
   object versions and timestamps.
2. Stop candidate Django and Caddy, keep candidate timers disabled, confirm no
   target media tasks are running, and recreate the candidate `signage`
   database as an empty database owned by the non-superuser `signage_owner`
   after proving the exact target. Keep the locally generated
   `duducar_admin`, `signage_owner`, and `signage_app` cluster roles.
3. Verify the SHA-256 sidecar and run `pg_restore --list` against the archive,
   apply the reviewed runtime-grant SQL, then restore the complete
   custom-format dump with `--no-owner --no-privileges --role signage_owner`.
   Reapply the runtime grants after the restore. Do not run migrations before
   this restore and never restore over a populated candidate.
4. Run `sudo /usr/local/sbin/duducar-command migrate`, reapply
   `sudo /usr/local/sbin/duducar-command grant-runtime`, and require
   `sudo /usr/local/sbin/duducar-command migration-check` to pass. Then start
   Django and run `sudo /usr/local/sbin/duducar-command readiness`.
5. Compare users, devices, assignments, immutable playlist versions, media
   metadata, playback-event counts, audit-event counts, and representative
   reports between RDS and the candidate.
6. Upload disposable rehearsal media through the candidate. Confirm it
   dispatches the `-ec2-media-worker` family, connects over TLS to the private
   EC2 address, and never starts the legacy continuous service.
7. Test signed CloudFront URLs for `validated/*`; verify unsigned, expired,
   modified, `quarantine/*`, and arbitrary-key requests return access denied.
8. Run the daily backup command, restore the archive into an isolated empty
   database, and restore a DLM snapshot to a separate volume. Record an RPO and
   RTO below 24 hours before approving cutover.

The DLM snapshot is crash-consistent; PostgreSQL WAL recovery and the
application-level S3 backup are both required recovery layers. Snapshot
creation alone is not a restore test.

## Historical: pre-cutover verification

With the preflight Caddyfile, test locally through SSM without changing DNS:

```sh
curl --fail --insecure \
  --resolve marketing.duducaradmin.com:443:127.0.0.1 \
  https://marketing.duducaradmin.com/health/live/
curl --fail --insecure \
  --resolve api.marketing.duducaradmin.com:443:127.0.0.1 \
  https://api.marketing.duducaradmin.com/health/ready/
```

Load-test concurrent dashboard/API traffic and a realistic proof-of-play upload
batch. Watch EC2 CPU, memory from the host, PostgreSQL connections, free data
volume space, and CPU-credit balance. Native alarms cover host status and
sustained CPU plus low CPU credits. The instance uses standard T4g credits to
prevent surplus-credit charges, so sustained load can throttle it. Disk,
memory, database health, and TLS expiry remain operator checks. Scheduler
failure is surfaced through systemd/SNS, while the persistent host-health check
covers disk space and backup freshness without enabling costly
high-cardinality metrics.

Before the maintenance window, switch to
`/etc/duducar/Caddyfile.production` and redeploy while public DNS still points
at the legacy ALB. The highest-priority temporary listener rule forwards only
requests matching both production hostnames and
`/.well-known/acme-challenge/*` to candidate port 80. This lets Caddy complete
HTTP-01 and persist its public certificate on the encrypted data volume without
forwarding dashboard/API traffic.

Confirm the temporary target is healthy at `/health/live/`, then verify the
pre-issued certificate directly against the EIP with a local `--resolve`
override. Do not change DNS unless the certificate covers both production
hostnames and the complete candidate readiness gate passes.

## Historical: cutover procedure

This procedure was executed on 2026-07-28. It remains audit evidence only and
must not be replayed against current production:

1. Lower the application-record TTL in advance while
   `application_origin="alb"`.
2. Set `ecs_web_desired_count=0` and `enable_ecs_schedules=false`; review and
   apply that plan, then confirm no legacy web or scheduled task remains
   running.
3. Run the final logical RDS backup through the then-live scheduled task
   definition, then verify the archive and sidecar and record their object
   versions. That task definition is no longer an available production backup
   path; current backups run on the EC2 host with
   `sudo /usr/local/sbin/duducar-command backup`.
4. Stop the candidate stack, prove and recreate an empty candidate database,
   restore the complete final archive before running migrations, then start
   Django, migrate, run readiness, and reconcile immutable record counts.
5. Create and verify a fresh candidate-host backup. Explicitly enable the
   application and timers:

   ```sh
   sudo systemctl enable --now duducar.service
   sudo systemctl enable --now \
     duducar-health.timer \
     duducar-playlists.timer \
     duducar-media-reconcile.timer \
     duducar-retention.timer \
     duducar-backup.timer
   ```

6. Confirm the already-issued Caddy certificate remains valid, set
   `application_origin="ec2"`, review the in-place Route 53 record change, and
   apply it.
7. Verify public TLS, health, authentication, device sync, media processing,
   signed downloads, proof ingestion, schedules, backups, and alarms.
8. Keep ALB/ECS/RDS intact and quiesced for the rollback window. RDS becomes
   stale as soon as the candidate accepts a production write, so rollback is a
   second controlled data migration, not a Terraform-variable flip. Stop
   candidate writes and timers, wait for isolated candidate worker tasks to
   finish, create and verify a current candidate database backup, restore and
   reconcile it into the legacy database, and verify legacy readiness. Only
   then set `application_origin="alb"`, `ecs_web_desired_count=1`, and
   `enable_ecs_schedules=true`, review and apply, and finally disable the
   candidate timers.

That immediate reverse-migration path no longer exists after the completed
phase-two decommission. The retained RDS snapshot is stale historical data;
the authoritative recovery sources are current verified EC2 logical backups
and tested DLM snapshots. Change DNS only after the chosen rebuilt destination
passes record reconciliation and production readiness.

The preflight certificate is locally issued and must never be used as
production trust evidence. After DNS has converged on the EIP and renewal has
been checked directly, set `enable_ec2_acme_bridge=false` and apply a reviewed
plan that removes only the temporary listener rule, target attachment, and
target group. The bridge health check depends on the temporary plain-HTTP
`/health/live/` route, so remove the bridge before selecting
`/etc/duducar/Caddyfile.post-cutover` and redeploying. The post-cutover
configuration redirects ordinary HTTP to HTTPS; Caddy's internal ACME
challenge handler remains exempt.

At the time, legacy resources could be decommissioned only after the rollback
window, successful restore evidence, current replacement-host backups, and
explicit review of each staged plan.

## Historical: phase-two legacy decommission

All four stages below completed on 2026-07-28. They remain as an audit trail,
not an executable current procedure. There is no live legacy service to
quiesce and no live RDS instance whose deletion protection can be changed.

The decommission controls default to retaining everything:

```hcl
enable_legacy_ecs_runtime            = true
enable_legacy_alb                    = true
enable_legacy_rds                    = true
legacy_rds_deletion_protection       = true
legacy_rds_final_snapshot_identifier = ""
confirm_legacy_rds_final_snapshot    = false
```

Before changing a gate, apply the default-enabled address migration. Resources
converted from singleton to counted addresses must appear only as state-address
moves to `[0]`; they must not be created, replaced, or destroyed. Separate any
unrelated drift from this plan.

Keep this observation state for the approved rollback window:

```hcl
application_origin                = "ec2"
enable_ec2_target                 = true
ecs_web_desired_count             = 0
enable_ecs_schedules               = false
enable_continuous_media_worker     = false
enable_ec2_acme_bridge             = false
enable_legacy_ecs_runtime          = true
enable_legacy_alb                  = true
enable_legacy_rds                  = true
legacy_rds_deletion_protection     = true
confirm_legacy_rds_final_snapshot = false
```

During observation, verify public traffic, scheduled commands, database
backups, media dispatch, alarms, and proof-of-play ingestion from the EC2
target. Confirm no ECS web task, continuous media worker, or legacy schedule is
running. Keep recording the reverse-migration procedure because the retained
RDS database is no longer current.

Decommission in four separately reviewed stages:

1. Set `enable_legacy_ecs_runtime=false`. The expected removals are the
   quiesced legacy ECS services and legacy EventBridge schedules, targets, and
   schedule alarms. Retain the ECS cluster, general ECS task-failure rule,
   RDS-backed task definitions, ALB, and RDS. Confirm the candidate's systemd
   timers and isolated `-ec2-media-worker` task still work. Terraform rejects
   this gate unless DNS already uses the EC2 target and the legacy web count,
   schedules, and continuous worker are quiesced.
2. Set `enable_legacy_alb=false`. The guard requires the ECS runtime to be
   disabled, its desired counts and schedules to remain off, DNS to use the
   EC2 target, and the temporary ACME bridge to be removed. The reviewed plan
   should remove only the legacy ALB, listeners, web target group, and ALB
   alarms. The certificate and no-cost security-group definitions remain.
3. Prepare RDS deletion while retaining RDS. Set
   `legacy_rds_deletion_protection=false`, choose a new timestamped
   `legacy_rds_final_snapshot_identifier`, keep `enable_legacy_rds=true`, and
   keep `confirm_legacy_rds_final_snapshot=false`. Apply this preparation plan
   and independently verify that AWS reports deletion protection disabled.
   The snapshot identifier must be unused in the account and Region.
4. Create and verify a fresh logical backup from the current EC2 production
   database and repeat the documented restore check. Then set
   `enable_legacy_rds=false` and
   `confirm_legacy_rds_final_snapshot=true`. Review a saved plan that removes
   RDS, its alarms, and the remaining RDS-backed task-definition policies
   before applying it. Terraform stops managing the legacy ECS task-definition
   revisions, but `skip_destroy` retains those inactive revisions; no retained
   Terraform-managed service or schedule references them. Wait for the final
   RDS snapshot to become available and record restore evidence.

Never combine stages 3 and 4. When the RDS resource is absent from the desired
configuration, Terraform cannot first update the live instance; AWS will
reject deletion if protection is still enabled. The explicit snapshot name and
separate confirmation prevent an empty/default value from authorizing the
destroy. The final RDS snapshot contains the stale legacy database, not current
EC2 production data, so it does not replace the fresh logical backup.

After RDS deletion, rollback requires rebuilding a database from a verified
snapshot or logical backup and reconciling post-backup writes; changing the
booleans alone is not a rollback procedure.

The completed decommission intentionally retained the ECS cluster and isolated
on-demand production worker, along with the production EC2 instance/data
volume, ECR repository, S3 buckets, KMS key, and CloudFront media path. The
data volume has Terraform `prevent_destroy`, the instance has API termination
protection, and CloudFront retains its distribution on Terraform deletion. Any
later cleanup of those resources requires a separate dependency and recovery
review.

## Current recovery decision tree

- **Code regression:** deploy the previous image pinned by digest. Do not
  reverse database migrations; use backward-compatible migrations and a
  reviewed forward fix.
- **Database corruption:** stop writes and timers, preserve evidence, then
  restore the latest verified logical backup into a clean `signage` database
  using `docs/backup-restore.md`. Reconcile immutable counts before traffic.
- **Host or data-volume failure:** rebuild the root/configuration host from
  Terraform and pinned images, attach a tested DLM data-volume restore to an
  isolated host, complete WAL recovery and application validation, then move
  traffic.
- **Return to managed RDS:** provision a new RDS/runtime path and restore a
  current EC2 logical backup. Never flip the historical legacy booleans and
  never rely only on the stale final RDS snapshot.

Recovery evidence completed on 2026-07-28:

- the production logical archive
  `database-backups/duducar-signage-postgres-20260728T031801Z.dump` and its
  exact S3 object versions passed checksum/catalogue validation and an isolated
  restore;
- the restored logical database and cloned EBS data volume both reported 26
  migrations and aggregate counts `1|0|0|3|10|59|0|0` for users, devices,
  drivers, media, playlists, audit events, playback batches, and playback
  events;
- Django connected through the restricted `signage_app` role and production
  readiness passed;
- the temporary restore database, cloned volume, and rehearsal snapshot were
  removed after verification; and
- the frozen-cutover restore had zero observed data loss and completed well
  inside the 24-hour RTO. Daily operation retains the designed RPO and RTO of
  at most 24 hours; each future rehearsal must record its measured values.

## Post-migration live verification — 2026-07-30

- A saved, refreshed Terraform plan proposed no AWS resource change or
  replacement. It detected the already-deployed IAM/KMS/SNS alert-policy fixes
  as remote state refreshes matching the current configuration, plus five new
  output aliases that would change Terraform state only. Nothing was applied.
- Production had one running `t4g.small`, encrypted 8 GB and 32 GB volumes,
  termination protection, IMDSv2, public ingress limited to TCP 80/443, no SSH,
  no ALB, and no live RDS instance.
- ECS had no service, pending task, running task, or application schedule. At
  the start of this verification, historical task-definition revisions remained
  registered at no runtime cost, but the EC2 instance role could run only the
  current isolated media-worker task definition.
- The USD 30 project-tagged budget, all five thresholds, three healthy
  CloudWatch alarms, and the confirmed operations email subscription were
  present. Tagged billing data was reporting but did not yet have a forecast.
- The latest versioned logical backup was less than 24 hours old; its sidecar
  checksum and `pg_restore` catalogue passed. Both S3 buckets remained
  KMS-encrypted, versioned, non-public, and protected by all public-access
  blocks.
- CloudFront OAC, trusted key groups, HTTPS redirect, and unsigned-object denial
  passed. Both application hostnames resolved to the production EIP, returned
  healthy HTTPS responses, redirected HTTP, presented certificates valid for
  more than seven days, and returned the required application security headers.
- SSM production readiness, stack status, all three runtime containers, all
  five systemd timers, and the zero-failed-unit check passed.
- The manual bootstrap snapshot remains intentionally retained until an
  isolated restore of the exact DLM-managed snapshot is recorded.

### Historical ECS registration cleanup — 2026-07-30

After a second dependency audit confirmed zero ECS services, running or pending
tasks, EventBridge ECS targets, Scheduler ECS schedules, or current IAM
references, these historical revisions were deregistered:

- `duducar-signage-production:11`
- `duducar-signage-production-scheduled:10`
- `duducar-signage-production-worker:10`
- `duducar-signage-production-rollback-20260728:1`

The ECS cluster, SNS task-failure rule, and
`duducar-signage-production-ec2-media-worker:1` remain active. The production
EC2 role's only `ecs:RunTask` grant targets that current worker revision. The
obsolete arbitrary ECS management-command wrapper was removed from the
repository after confirming that no runtime, Terraform, CI, or test referenced
it.

## Known limitations and residual risk

- The production web/database host is single-AZ and is a single point of
  failure. The accepted pilot RPO/RTO is maintained through daily backups,
  snapshots, a rebuildable host, and tested restore procedures.
- There is no WAF in the USD 30 target. Security depends on Caddy TLS, Django
  security controls and rate limits, least-privilege IAM, Security Groups,
  patching, and AWS Shield Standard.
- The public HTTPS probes originate on the production host. They catch
  DNS/Caddy/TLS/routing failures while the host is alive, but they are not an
  independent external uptime monitor.
- The fixed JSON `/health/live/` and `/health/ready/` responses are returned by
  the first middleware and therefore include the custom CSP, permissions, and
  opener headers but not Django's HSTS, `nosniff`, or frame headers. Normal
  application routes include the complete header set. Align the health
  responses in a separately tested backend hardening change.
- IMDSv2 is required, but its hop limit of two permits container access to the
  shared instance role. Container hardening and least-privilege IAM reduce,
  but do not remove, the credential blast radius of a web-process compromise.
- A public subnet and EIP are required to avoid NAT/ALB cost. PostgreSQL is not
  internet-open; Security Groups allow it only from the worker group, and
  `pg_hba.conf` rejects plaintext/non-SCRAM network connections.
- The tag-filtered budget is not a complete tax-inclusive bill.
- CloudFront free allowances are shared at account/organization scope and must
  be monitored.
- CloudFront `retain_on_delete` prevents a one-step Terraform cleanup and may
  leave a disabled or retained distribution requiring an explicit, separately
  reviewed cleanup.
- The default `*.cloudfront.net` certificate exposes AWS's `TLSv1` policy
  value, although Android 12 and current browsers negotiate modern TLS. A hard
  TLS 1.2 minimum requires a custom media hostname and ACM certificate in
  `us-east-1`; Terraform records the actual AWS policy instead of maintaining
  an unenforceable perpetual diff.
- DLM incremental snapshot size and Fargate processing time vary with real
  media volume.
- Tagged ECR rollback releases are retained indefinitely; review and cap older
  tagged releases after the migration observation window to prevent slow
  recurring-storage growth.
- Root disk and data disk are encrypted with AWS-managed EBS encryption. The
  project KMS key remains actively required by private S3 backups/media, ECR
  images, CloudFront media delivery, and the retained encrypted final RDS
  snapshot.
