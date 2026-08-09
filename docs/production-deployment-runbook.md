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
- The final production APK version name and version code, plus the matching
  `required_app_version` in the reviewed production tfvars. These values must
  be identical for the canary or every heartbeat will raise an outdated-app
  alert.
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
   task definition changes. Explicitly approve its backup-bucket lifecycle
   consequence before applying: the current release changes noncurrent backup
   versions from 30 days to one day. The expected steady-state plan must not
   recreate an ECS web service, Fargate schedules, ALB, live RDS instance, NAT
   Gateway, or public S3 access.
6. When media dispatch changes, apply the reviewed IAM and worker-task portion
   before deploying the new web image. The web requires `ecs:ListTasks`, and
   the isolated worker must use the same reviewed backend digest. Never deploy
   code that calls a permission the host role does not yet have.
7. Keep all production secret values out of Terraform, command arguments,
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

Connect through Session Manager; port 22 is deliberately closed. Updating the
existing host never replays Terraform user data. First apply the reviewed
Terraform plan that creates or updates the zero-cost SSM command documents.
The release worktree must be clean so that the full reviewed Git commit and
embedded hashes describe the same source.

Do not hand-edit, copy, or rewrite `/etc/duducar/release.env` during a release.
The pinned backend digest and `REQUIRED_APP_VERSION` must change together
through the dedicated `production_release_config_document`. It accepts only
values that exactly match the Terraform release selection embedded in that
document: a digest in the reviewed backend ECR repository and a semantic
Android version. The document changes no other assignment, preserves a
root-only per-operation backup beneath
`/var/lib/duducar/release-config-backups/<commit>-<operation-id>/`, and never
prints the configuration or restarts services. The runtime renderer sources
this release file after bootstrap-only `host.env`, so this is the deliberate
live override for the host. Read the digest and required version from applied
Terraform outputs to keep the host and isolated media-worker selection equal;
independently verify the version equals the signed APK's version name before
creating the operation:

```sh
test -z "$(git status --porcelain)" || {
  echo "Refusing to label a release from a dirty worktree." >&2
  exit 1
}
production_instance=$(terraform -chdir=infrastructure/terraform output -raw \
  production_host_instance_id)
reviewed_commit=$(git rev-parse HEAD)
release_config_document=$(terraform -chdir=infrastructure/terraform output -raw \
  production_release_config_document)
release_config_document_version=$(terraform -chdir=infrastructure/terraform output -raw \
  production_release_config_document_version)
release_config_document_hash=$(terraform -chdir=infrastructure/terraform output -raw \
  production_release_config_document_hash)
release_config_document_hash_type=$(terraform -chdir=infrastructure/terraform output -raw \
  production_release_config_document_hash_type)
release_config_operation_id=$(openssl rand -hex 16)
release_backend_image=$(terraform -chdir=infrastructure/terraform output -raw \
  production_backend_image)
release_required_app_version=$(terraform -chdir=infrastructure/terraform output -raw \
  production_required_app_version)

test "${#release_config_operation_id}" -eq 32
test -n "$release_backend_image"
test -n "$release_required_app_version"
test "$(sha256sum infrastructure/terraform/ec2/runtime/manage-release-config | \
  awk '{print $1}')" = "$(terraform -chdir=infrastructure/terraform output -raw \
  production_release_config_manager_sha256)"
```

Submit `Mode=validate` first, wait for a `Success` invocation, and inspect the
non-secret confirmation. Only then use the same pinned document version/hash,
commit, operation ID, backend digest, and app version with `Mode=install`:

```sh
config_validation_command=$(aws ssm send-command \
  --profile dudu-production --region ap-southeast-5 \
  --document-name "$release_config_document" \
  --document-version "$release_config_document_version" \
  --document-hash "$release_config_document_hash" \
  --document-hash-type "$release_config_document_hash_type" \
  --instance-ids "$production_instance" --timeout-seconds 60 \
  --parameters "Mode=validate,ExpectedCommit=$reviewed_commit,OperationId=$release_config_operation_id,BackendImage=$release_backend_image,RequiredAppVersion=$release_required_app_version" \
  --query 'Command.CommandId' --output text)
```

Use the bounded SSM polling helper in `infrastructure/README.md` (or poll
`aws ssm get-command-invocation` explicitly) until
`config_validation_command` is `Success`. Record and review its result before
continuing. Then require an operation-specific confirmation:

```sh
printf 'Type INSTALL CONFIG %s to continue: ' "$release_config_operation_id"
read -r release_config_confirmation
test "$release_config_confirmation" = "INSTALL CONFIG $release_config_operation_id"

install_config_command=$(aws ssm send-command \
  --profile dudu-production --region ap-southeast-5 \
  --document-name "$release_config_document" \
  --document-version "$release_config_document_version" \
  --document-hash "$release_config_document_hash" \
  --document-hash-type "$release_config_document_hash_type" \
  --instance-ids "$production_instance" --timeout-seconds 60 \
  --parameters "Mode=install,ExpectedCommit=$reviewed_commit,OperationId=$release_config_operation_id,BackendImage=$release_backend_image,RequiredAppVersion=$release_required_app_version" \
  --query 'Command.CommandId' --output text)
```

Use `aws ssm get-command-invocation` to require `Success` for each command and
record both command IDs, the document version/hash, and the operation ID. The
install command is retry-safe only with the same inputs. If it must be rolled
back **before any schema migration begins**, submit the same document and
inputs with `Mode=rollback`; it restores only the matching saved `release.env`
and does not restart services. The rollback refuses a stale operation rather
than discarding a later configuration change:

```sh
printf 'Type ROLLBACK CONFIG %s to continue: ' "$release_config_operation_id"
read -r release_config_confirmation
test "$release_config_confirmation" = "ROLLBACK CONFIG $release_config_operation_id"

rollback_config_command=$(aws ssm send-command \
  --profile dudu-production --region ap-southeast-5 \
  --document-name "$release_config_document" \
  --document-version "$release_config_document_version" \
  --document-hash "$release_config_document_hash" \
  --document-hash-type "$release_config_document_hash_type" \
  --instance-ids "$production_instance" --timeout-seconds 60 \
  --parameters "Mode=rollback,ExpectedCommit=$reviewed_commit,OperationId=$release_config_operation_id,BackendImage=$release_backend_image,RequiredAppVersion=$release_required_app_version" \
  --query 'Command.CommandId' --output text)
```

Require `rollback_config_command` to reach `Success`, then render the restored
runtime environment before any previous compatible stack deployment. Never use
this mechanical configuration rollback after applying the battery-backed policy
migration to select an older app image.

If the checked-in `Caddyfile.post-cutover` or `render-runtime-env` changed,
also run the existing `production_runtime_asset_document` with its exact pinned
version and hash once with `Mode=validate`, then with `Mode=install`. Its
installation preserves the previous files beneath
`/var/lib/duducar/runtime-backups/<commit>-<operation-id>/` and does not
restart services. If Caddy itself changes, pre-pull its reviewed digest and
pass that digest as `CaddyImage` during runtime validation; never use a mutable
tag. Record the document version/hash, operation ID, Caddy digest, validation
and install command IDs, plus any rollback command ID. Do not use a generic
file-copy command or replay cloud-init against the live host.

After both relevant documents install successfully, render runtime
configuration without printing secret values and verify the effective version:

```sh
sudo /usr/local/sbin/render-duducar-runtime-env
sudo awk -F= '$1 == "REQUIRED_APP_VERSION" { print $0 }' \
  /run/duducar/application.env
```

Before running migration `0009`, use a separate browser session to verify that
an active account-owner can sign in and reach the dashboard. Record that
preflight in the change record; do not infer it from a database user count or a
marketing-user login. The migration cannot restore marketing administrator
flags or the terminated sessions.

Before applying `0010_battery_backed_player_policy`, record in the change
record the count and identities of active devices linked to qualifications that
will become legacy/unapproved. Confirm, in the reviewed release and during the
maintenance window, that each affected device receives `maintenance` from sync
until its exact model and firmware are requalified under the battery-backed
criteria; enrollment-only qualification checks are insufficient for an
already-active credential. Also list unresolved historical alert codes from the
retired power-loss policy (including `repeated_power_loss` and
`long_power_interruption`) and have an authorized dashboard user manually
triage and acknowledge them where appropriate. Preserve the historical alerts
as evidence; do not silently close or delete them during the migration.

`0010_battery_backed_player_policy` is not a normal old-image rollback point.
Its legacy columns exist only for historic/read compatibility; they do not
restore the former external-power policy or authorize a pre-policy image in
production. After `0010` is recorded, keep the new image selected and resolve
a code failure with a reviewed forward fix. A pre-policy image may be used only
for read-only investigation against an isolated restored recovery point, never
as a live rollback. Do not reverse the migration casually.

```sh
sudo /usr/local/sbin/duducar-command migrate
sudo /usr/local/sbin/duducar-command grant-runtime
sudo /usr/local/sbin/duducar-command migration-check
```

Migration `0009_revoke_marketing_admin_access` intentionally removes legacy
marketing-admin flags and flushes all dashboard sessions so old one-time
secrets cannot remain in session storage. Notify the pilot dashboard users
of the scheduled forced logout and require them to sign in again after this
migration.

Deploy and verify the pinned stack:

```sh
sudo /usr/local/sbin/duducar-stack deploy
sudo /usr/local/sbin/duducar-stack status
sudo /usr/local/sbin/duducar-command readiness
```

If the release changes media processing, confirm the already-applied isolated
worker task definition uses the same application digest. Do not start a polling
worker or an ECS web service.

## Qualify hardware before enrollment

Run every test in [`hardware-qualification.md`](hardware-qualification.md) and
store photos, logs, video, and notes in restricted company storage. Create and
approve the `HardwareQualification` record for the exact model and firmware,
then link it when provisioning the device. The production enrollment-challenge
endpoint rejects unqualified devices even if their integrity verdict passes.

Do not infer kiosk, battery-backed playback, planned shutdown, screen,
temperature, or thermal support from an emulator or phone. The exact model and
firmware must pass the current battery-backed criteria, including battery
runtime/telemetry, planned shutdown, physical shutdown/recovery, and
abnormal-exit recovery; legacy auto-boot and vehicle-power-loss results do not
qualify a new record. If `MEETS_DEVICE_INTEGRITY` cannot be obtained on the
exact tablet, leave vehicle enrollment disabled and launch only the dashboard/API.

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
7. Verify atomic sync, hashes, playback, fallback, the battery-backed heartbeat
   contract (new APKs omit external-power and charging telemetry), proof upload,
   report filters, finalization state, and CSV privacy. Confirm reports do not
   claim vehicle operation, occupancy, external power, or audience exposure.
8. Verify replayed, expired, forged, and wrong-hash integrity enrollment fails
   without consuming a valid code.
9. Rehearse offline sync/upload, interrupted or corrupt downloads, external
   cable/vehicle-power disconnection while battery playback continues,
   server-clock offset, urgent and normal replacement, disable/reactivate,
   low-storage forced-loss recording, kiosk escape, and PIN reset. Exercise
   `<=20%`/`<=10%` low-battery escalation without stopping advertising; the
   visible non-PIN **Prepare for shutdown** confirmation, neutral stopped
   screen, and visible non-PIN **Resume DUDU** confirmation. Merely restoring
   the launcher, activity, or lifecycle must not restart advertising. Rehearse
   the documented physical shutdown and staff-unlock/launch procedure; Android
   13 abnormal-exit diagnostics where applicable; and controlled
   server-received-heartbeat checks for the 24-hour warning and 48-hour
   critical `device_unavailable` escalation.
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

For a code regression, use a reviewed forward fix and keep database migrations
backward-compatible; never reverse a production migration casually. Before any
schema migration, a guarded release-config rollback may restore its exact saved
selection. After `0010_battery_backed_player_policy`, do **not** pin a
pre-policy image as a normal live rollback: historic-column support is
old-image reads only on an isolated recovered data set, not restoration of the
former policy. The safe live path is the released image or a reviewed forward
fix. If media processing is implicated, stop new uploads and allow isolated
tasks to finish before changing the worker definition.

For database or host failure, stop writes and timers, preserve evidence, and use
the current logical-backup or DLM recovery path. Do not point DNS or the
production Elastic IP at a recovered host until reconciliation, readiness, and
owner approval pass. Restoring the retained final RDS snapshot would recover
only stale pre-cutover data and requires a new isolated RDS migration plan.

An Android rollback, when independently compatibility-reviewed, remains staff
sideloading of an APK signed by the same release key. It does not authorize a
pre-policy backend image, restore the former external-power policy, or bypass
the current hardware-qualification gate.

## Go/no-go

The canary may start only when production readiness, CI, vulnerability review,
SMTP, private S3/CloudFront media processing, integrity rejection tests,
exact-hardware qualification, offline/battery-backed-playback/planned-
shutdown/abnormal-exit/replacement/disablement tests, proof idempotency,
current backup and restore evidence, notification delivery, and the USD 30
estimate all pass. A completed battery-powered or parked play counts, but it is
not evidence of vehicle operation, occupancy, external power, or audience
exposure.

If integrity, hardware, restore, private-media, notification, budget, or
proof-idempotency gates fail, keep production vehicle enrollment disabled. A
live dashboard/API with no passenger-facing player is the approved fallback.
