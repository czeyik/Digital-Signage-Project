# Production Deployment And Canary Runbook

This week's acceptable launch is a staff-controlled production canary on one
qualified tablet, followed by a second tablet only after the first is stable.
An unrestricted 10-vehicle launch is not authorized by this runbook.

## Owner prerequisites

Before engineering can deploy, the project owner must complete and confirm:

- AWS production account root MFA, no root access keys, an MFA-backed deployer,
  billing contacts, and RM400/RM450/RM500 budget notifications.
- AWS CLI access using temporary credentials and permission to use
  `ap-southeast-5`.
- Route 53/DNS control for `duducaradmin.com`, with both production hostnames
  unused and available.
- Company SMTP host, port, TLS mode, dedicated username/app credential,
  `no-reply@duducar.co`, SPF/DKIM/DMARC, and two test inboxes. Enter credentials
  directly in Secrets Manager; never send them through chat or Git.
- Google Cloud project, Play Integrity API, numeric project number, and a
  dedicated decode service account. Store its JSON directly in Secrets Manager.
- A protected Android release keystore, passwords in the company vault, an
  encrypted offline backup, and the certificate SHA-256 fingerprint.
- Two Play Protect-certified Android 12+ canary tablets, data SIMs, assignments,
  test media, contacts, approver, and rollback window.
- GitHub `main` designated as authoritative, protected from direct pushes, with
  Actions, secret scanning, and security alerts enabled.

The owner must review an AWS Pricing Calculator estimate before applying. Stop
if normal monthly cost exceeds RM500, excluding tablet mobile data.

## Deploy infrastructure

Follow [`infrastructure/README.md`](../infrastructure/README.md) exactly. It
bootstraps remote encrypted Terraform state, provisions the VPC/ALB/ACM/ECS/ECR,
private S3, encrypted Single-AZ RDS, Secrets Manager, schedules, alarms, DNS,
and budget notifications, then runs migrations and production readiness before
starting services.

Production secrets are not Terraform inputs. Enter them as the documented JSON
value directly in the generated Secrets Manager secret. Keep the immutable image
digest/task revision and previous known-good APK for rollback.

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

1. Verify both HTTPS hostnames, HTTP redirect, `/health/live/`, and
   `/health/ready/`.
2. Test login, logout audit, lockout, password reset expiry/single-use/session
   invalidation, and role/driver-name restrictions.
3. Upload one JPEG, PNG, and MP4. Confirm private quarantine, current ClamAV
   definitions, full decode, normalization, audio removal, and signed preview.
4. Publish a weekly playlist and verify missing-replacement warning behavior.
5. Provision an assigned device linked to its approved hardware qualification,
   generate a 15-minute code, and enroll the release-signed APK.
6. Verify atomic sync, hashes, playback, fallback, heartbeat, proof upload,
   report filters, finalization state, and CSV privacy.
7. Verify replayed/expired/forged/wrong-hash integrity enrollment fails without
   consuming a valid code.
8. Rehearse offline sync/upload, interrupted/corrupt downloads, power loss,
   server-clock offset, urgent/normal replacement, disable/reactivate,
   low-storage forced-loss recording, kiosk escape, and PIN reset.
9. Confirm one complete loop appears exactly once and only fully completed real
   advertisements count as contractual plays.
10. Trigger and acknowledge representative alerts, then verify scheduler,
    worker, ALB/ECS/RDS, backup, and budget alarms.

## Backup and rollback gate

Follow [`backup-restore.md`](backup-restore.md). Prove the application archive,
an isolated RDS restore, and recovery of an S3 object version. Validate login and
sample reports on restored data and record elapsed recovery time under 24 hours.

For a web regression, deploy the preceding ECS task-definition revision. Stop
the worker first if media processing is implicated. Never reverse a production
migration without a reviewed restore plan. Android rollback is staff sideloading
of the previous APK signed by the same release key.

## Go/no-go

The canary may start only when production readiness, CI, vulnerability review,
SMTP, real S3 media processing, integrity rejection tests, exact-hardware
qualification, offline/power/replacement/disablement tests, proof idempotency,
restore drill, alarms, and the RM500 estimate all pass.

If integrity, hardware, restore, private-media, or proof-idempotency gates fail,
keep production vehicle enrollment disabled. A live dashboard/API with no
passenger-facing player is the approved fallback.
