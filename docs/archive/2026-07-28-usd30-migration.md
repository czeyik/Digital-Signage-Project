# USD 30 Production Migration Record

> **Archive only.** This summarizes the completed 2026-07-28 migration. Do not
> replay its historical steps. Use the [production runbook](../production-deployment-runbook.md),
> [recovery runbook](../backup-restore.md), and
> [infrastructure guide](../../infrastructure/README.md) for current operations.

## Outcome

Production moved from ECS web services, an Application Load Balancer, and RDS
to the pilot-scale EC2 topology now defined in
[`docs/architecture.md`](../architecture.md). DNS points to the EC2 Elastic IP;
systemd manages Caddy, Django, PostgreSQL, and five maintenance timers. The ECS
cluster remains only for isolated, on-demand media processing.

The migration targeted a tax-inclusive steady-state cost of about USD 25–28
per month, with a USD 30 project budget. The July 2026 bill included both old
and new stacks and is not a valid steady-state measurement.

## Migration summary

The approved migration:

1. provisioned the EC2 host, encrypted data volume, private CloudFront media
   path, DLM policy, alarms, and isolated worker without moving traffic;
2. restored and reconciled a logical RDS backup on the candidate, exercised a
   snapshot clone, private media, readiness, and bounded load;
3. quiesced legacy writers, took a final backup, restored current data, enabled
   systemd services and timers, then moved DNS to the EC2 Elastic IP;
4. observed the replacement before separately removing legacy ECS runtime,
   ALB, and RDS resources; and
5. removed the temporary ACME bridge and obsolete task definitions after
   dependency checks.

These steps describe history, not a rollback path. The former services no
longer exist, and the retained RDS snapshot contains stale pre-cutover data.

## Acceptance evidence

The 2026-07-28 cutover recorded:

- valid public TLS, HTTPS redirects, readiness, and security headers;
- signed private-media delivery, with unsigned, expired, modified,
  quarantined, and arbitrary object requests denied;
- checksum/catalogue verification and an isolated restore of
  `database-backups/duducar-signage-postgres-20260728T031801Z.dump`;
- a separate EBS snapshot clone restored through PostgreSQL and Django;
- 26 migrations and matching aggregates `1|0|0|3|10|59|0|0` for users,
  devices, drivers, media, playlists, audit events, playback batches, and
  playback events;
- runtime DDL denied to `signage_app`, with readiness passing through that
  restricted role;
- a bounded 400-request test with no unexpected responses;
- successful encrypted-volume, container, and timer recovery after reboot;
- an isolated media-worker task that connected to EC2 PostgreSQL and exited
  successfully; and
- successful CloudWatch ALARM/OK and failed-task EventBridge/SNS delivery
  after correcting KMS publisher permissions.

Temporary restore resources and the rehearsal snapshot were removed. The
frozen-cutover restore had no observed data loss and completed within the
24-hour RTO; ongoing recovery still depends on current, tested backups.

## Retained artifacts and later verification

- One encrypted final RDS snapshot was retained for review through 2026-08-27.
  It is historical evidence, not a direct rollback source.
- As of 2026-07-30, a manual encrypted 32-GiB bootstrap snapshot remained
  pending a successful isolated restore of the exact DLM-managed recovery
  point. A completed snapshot alone is not restore evidence.
- The 2026-07-30 read-only review found an empty Terraform plan, one protected
  `t4g.small`, encrypted 8-GiB/32-GiB volumes, no SSH/ALB/live RDS, no running
  ECS service or schedule, healthy alarms, current logical backup, private
  versioned S3 buckets, signed CloudFront delivery, working public routes,
  three runtime containers, and five active timers.
- Historical application, schedule, worker, and rollback task-definition
  revisions were deregistered after dependency review. The ECS cluster,
  failed-task notification rule, and current isolated worker remain.

Deletion of either retained snapshot requires a current restore review and a
separately approved cleanup. Git history retains the command-level migration
record if an audit needs it; those commands must not be reused operationally.

## Recovery boundary

Current recovery uses verified EC2 logical backups or tested DLM snapshots:

- use a reviewed forward fix for code regressions;
- stop writes and restore into a clean database for corruption;
- rebuild the root host and validate an isolated data-volume clone for host or
  volume failure; and
- treat any return to managed RDS as a new, costed migration.

Never restore service by toggling historical Terraform controls. Follow
[`docs/backup-restore.md`](../backup-restore.md) for the authoritative sequence.

## Residual risk at migration completion

- Django and PostgreSQL share one single-AZ host. Daily logical backups, DLM
  snapshots, and tested recovery support the accepted 24-hour RPO/RTO.
- The USD 30 design has no WAF, NAT gateway, managed database, or independent
  external uptime monitor. Security depends on Caddy/Django controls,
  least-privilege IAM, security groups, patching, and AWS Shield Standard.
- Host-originated public probes detect DNS/TLS/routing failures only while the
  host is running. Disk, memory, database health, and certificate expiry remain
  operator checks.
- IMDSv2 is required, but a hop limit of two allows containers to reach the
  host role; container hardening and limited IAM reduce but do not remove that
  blast radius.
- Tagged budgets can omit tax or unallocated charges. Snapshot growth, media
  egress, Fargate duration, and shared CloudFront allowances require continued
  measurement.
- CloudFront retention and rollback-image retention require explicit later
  cleanup reviews; neither should be removed as incidental Terraform drift.
