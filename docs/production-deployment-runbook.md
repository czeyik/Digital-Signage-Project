# Production Change And Canary Runbook

Production is already live in `ap-southeast-5`. One ARM `t4g.small` runs
Caddy, Django, and PostgreSQL under systemd; private S3 media is delivered
through CloudFront signed URLs; media processing uses one isolated Fargate task
per dispatch. The legacy ECS web services and schedules, ALB, and live RDS
instance were removed on 2026-07-28 and are not a rollback path.

The next authorized player launch is a staff-controlled canary on one qualified
tablet, followed by a second tablet only after the first is stable. An
unrestricted 10-vehicle launch is not authorized by this runbook.

## Owner prerequisites

Before a production change or device enrollment, the project owner must confirm:

- AWS production account root MFA, no root access keys, an MFA-backed SSO
  operator, billing contacts, and the project-filtered USD 30 budget alerts.
- AWS CLI access using temporary credentials and permission to use
  `ap-southeast-5`.
- Route 53 control for `duducaradmin.com`, and valid DNS/TLS for
  `marketing.duducaradmin.com` and `api.marketing.duducaradmin.com`.
- Company SMTP host, port, TLS mode, dedicated username/app credential,
  approved sender, SPF/DKIM/DMARC, and two test inboxes. Enter credentials
  directly in Secrets Manager; never send them through chat or Git.
- Google Cloud project, Play Integrity API, numeric project number, and a
  dedicated decode service account. Store its JSON directly in Secrets Manager.
- A protected Android release keystore, passwords in the company vault, an
  encrypted offline backup, and the certificate SHA-256 fingerprint.
- Two Play Protect-certified Android 12+ canary tablets, data SIMs, assignments,
  test media, contacts, approver, and rollback window.
- The authoritative release commit is reviewed and protected by CI, secret
  scanning, and security alerts.

Review [`aws-cost-estimate.md`](aws-cost-estimate.md) before any infrastructure
change. Stop if the projected project run rate exceeds USD 30 per month,
excluding the RM 40 monthly mobile-data allowance per tablet, until the owner
approves a cost reduction or an explicit new target.

## Prepare a production change

1. Start from a clean, reviewed release commit. Run the backend, Android, image,
   and Terraform checks applicable to the change.
2. Build and scan the Linux/ARM64 backend image. Reject unresolved critical or
   high-severity findings, then pin the release by immutable image digest.
3. Create a fresh logical PostgreSQL backup and verify its checksum and archive
   catalogue. Confirm a current DLM-managed data-volume snapshot and record both
   recovery points.
4. Confirm the current non-secret topology controls still select the EC2
   origin, private CloudFront delivery, disabled legacy ECS/ALB/RDS resources,
   disabled continuous worker, and disabled Container Insights. Use
   `infrastructure/terraform/production-controls.tfvars.example` as the
   reviewed reference; never replace the live untracked variables with generic
   staging defaults.
5. Create and inspect a saved Terraform plan when infrastructure or the worker
   task definition changes. The expected steady-state plan must not recreate an
   ECS web service, Fargate schedules, ALB, live RDS instance, NAT Gateway, or
   public S3 access.
6. Keep all production secret values out of Terraform, command arguments,
   shell history, logs, Git, and chat. Enter rotations directly through the
   approved Secrets Manager path.

Use `infrastructure/README.md` as the current production operator guide. The
cutover commands retained under `Historical:` headings in
[`archive/2026-07-28-usd30-migration.md`](archive/2026-07-28-usd30-migration.md)
are audit evidence only and must not be replayed against the post-migration
state.

## Deploy the EC2 release

Use a reviewed maintenance window. Ensure no isolated media task is running
before changing database schema or worker code.

Connect through Session Manager; port 22 is deliberately closed. Update the
root-owned `/etc/duducar/release.env` to the reviewed image digests, keeping the
post-cutover Caddy configuration. Render runtime configuration without printing
secret values:

```sh
sudo /usr/local/sbin/render-duducar-runtime-env
```

For a backward-compatible Django migration, run the owner-scoped workflow
before deploying the new web container:

```sh
sudo /usr/local/sbin/duducar-command migrate
sudo /usr/local/sbin/duducar-command grant-runtime
sudo /usr/local/sbin/duducar-command migration-check
```

Deploy and verify the pinned stack:

```sh
sudo /usr/local/sbin/duducar-stack deploy
sudo /usr/local/sbin/duducar-stack status
sudo /usr/local/sbin/duducar-command readiness
```

If the release changes media processing, apply the reviewed Terraform plan so
the isolated worker task definition uses the same application digest. Do not
start a polling worker or an ECS web service.

## Qualify hardware before enrollment

Run every test in [`hardware-qualification.md`](hardware-qualification.md) and
store photos, logs, video, and notes in restricted company storage. Create and
approve the `HardwareQualification` record for the exact model and firmware,
then link it when provisioning the device. The production enrollment-challenge
endpoint rejects unqualified devices even if their integrity verdict passes.

Do not infer kiosk, power, screen, temperature, or thermal support from an
emulator or phone. If `MEETS_DEVICE_INTEGRITY` cannot be obtained on the exact
tablet, leave vehicle enrollment disabled and launch only the dashboard/API.

## Production rehearsal

1. Verify both HTTPS hostnames, HTTP-to-HTTPS redirect, `/health/live/`, and
   `/health/ready/` from outside the EC2 host.
2. Test login, logout audit, lockout, password-reset
   expiry/single-use/session invalidation, and role/driver-name restrictions.
3. Upload one JPEG, PNG, and MP4. Confirm private quarantine, current ClamAV
   definitions, full decode, normalization, audio removal, and signed preview.
   Confirm one isolated worker task runs and exits; no continuous worker should
   exist.
4. Confirm unsigned, expired, modified, quarantined, and arbitrary CloudFront
   object requests are denied.
5. Publish a weekly playlist and verify missing-replacement warning behavior.
6. Provision an assigned device linked to its approved hardware qualification,
   generate a 15-minute code, and enroll the release-signed APK.
7. Verify atomic sync, hashes, playback, fallback, heartbeat, proof upload,
   report filters, finalization state, and CSV privacy.
8. Verify replayed, expired, forged, and wrong-hash integrity enrollment fails
   without consuming a valid code.
9. Rehearse offline sync/upload, interrupted or corrupt downloads, power loss,
   server-clock offset, urgent and normal replacement, disable/reactivate,
   low-storage forced-loss recording, kiosk escape, and PIN reset.
10. Confirm one complete loop appears exactly once and only fully completed
    advertisements count as contractual plays.
11. Verify all five systemd timers, the logical-backup alert, host disk and
    public-route health checks, EC2 status/CPU/credit alarms, EventBridge
    notification for a deliberately failed isolated task, SNS email delivery,
    and the USD 30 budget alerts.

## Backup and rollback gate

Follow [`backup-restore.md`](backup-restore.md). Prove an exact versioned logical
archive restore into an isolated empty PostgreSQL database, an isolated clone
from a DLM data-volume snapshot, and recovery of matching private-media object
versions. Validate login and sample reports on restored data and record a
recovery time under 24 hours.

For a code regression, pin the previous known-good image digest and redeploy the
EC2 stack. Keep database migrations backward-compatible and use a reviewed
forward fix; never reverse a production migration casually. If media processing
is implicated, stop new uploads and allow isolated tasks to finish before
changing the worker definition.

For database or host failure, stop writes and timers, preserve evidence, and use
the current logical-backup or DLM recovery path. Do not point DNS or the
production Elastic IP at a recovered host until reconciliation, readiness, and
owner approval pass. Restoring the retained final RDS snapshot would recover
only stale pre-cutover data and requires a new isolated RDS migration plan.

Android rollback remains staff sideloading of the previous APK signed by the
same release key.

## Go/no-go

The canary may start only when production readiness, CI, vulnerability review,
SMTP, private S3/CloudFront media processing, integrity rejection tests,
exact-hardware qualification, offline/power/replacement/disablement tests,
proof idempotency, current backup and restore evidence, notification delivery,
and the USD 30 estimate all pass.

If integrity, hardware, restore, private-media, notification, budget, or
proof-idempotency gates fail, keep production vehicle enrollment disabled. A
live dashboard/API with no passenger-facing player is the approved fallback.
