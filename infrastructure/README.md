# USD 30 Production Infrastructure Operations

This is the command reference for the production topology defined in
[`docs/architecture.md`](../docs/architecture.md): one ARM64 EC2 host, private
CloudFront/S3 media, on-demand Fargate processing, systemd timers, and layered
backups. ECS web services and schedules, ALB, live RDS, the continuous worker,
and Container Insights are retired and are not recovery paths.

The fleet basemap is self-hosted OpenMapTiles. Prepare and transfer the
verified MBTiles extract using [`docs/openmaptiles.md`](../docs/openmaptiles.md);
the runtime mounts it read-only and does not retrieve map data from a third
party API.

The Terraform names `ec2_target`, `migration_target`, and `legacy_*` are
migration-era state addresses. They are intentionally retained to avoid
replacing live resources or losing decommission history; they do not describe
the current topology.

## Operator safety boundary

Run infrastructure commands only from a clean, reviewed release commit with an
authenticated, MFA-backed production SSO session. Never put AWS credentials,
secret values, private signing keys, database passwords, or recovery material
in this repository or Terraform inputs.

The live `infrastructure/terraform/terraform.tfvars` is gitignored and is the
only authoritative variables file. Do not replace it with an example file.
[`terraform.tfvars.example`](terraform/terraform.tfvars.example) documents the
current topology but deliberately leaves the immutable image and CloudFront
public key empty, so an unreviewed copy cannot produce a valid plan.
[`production-controls.tfvars.example`](terraform/production-controls.tfvars.example)
contains only the non-secret post-migration switches for comparison.

For current production, these reviewed desired controls must remain effective:

```hcl
enable_services                    = false
enable_legacy_ecs_runtime          = false
enable_legacy_alb                  = false
enable_legacy_rds                  = false
ecs_web_desired_count              = 0
enable_ecs_schedules               = false
enable_continuous_media_worker     = false
enable_container_insights          = false
application_origin                 = "ec2"
enable_ec2_target                  = true
enable_media_cloudfront            = true
enable_ec2_acme_bridge             = false
```

`enable_services` is a deprecated outer compatibility gate. An older ignored
live variables file may still contain `enable_services=true`; that value cannot
create a service while the decisive `enable_legacy_ecs_runtime=false` guard
remains in place. The examples keep both values false as defense in depth.
Reconcile any vestigial live value only through a reviewed saved plan that
proves zero resource changes.

Do not set a legacy switch to `true`, select the ALB origin, or enable the ACME
bridge to troubleshoot current production. Recreating those retired resources
would increase cost and would not restore current EC2 PostgreSQL data.

`monthly_budget_usd` is the existing account-wide/shared-workload safety guard
and remains USD 115. `migration_budget_usd` is the authoritative
project-tagged USD 30 production target. Keep both budgets because tagged
budgets can omit tax and unallocated account charges.

## Terraform review

Initialize with the existing backend configuration:

```sh
terraform -chdir=infrastructure/terraform init \
  -backend-config=backend.hcl
terraform -chdir=infrastructure/terraform fmt -check
terraform -chdir=infrastructure/terraform validate
terraform -chdir=infrastructure/terraform plan -out=production.tfplan
```

Review the saved plan before applying it. Stop if it proposes any of the
following:

- creating an ALB, RDS instance, ECS web/continuous-worker service, or
  EventBridge application schedule;
- changing application DNS away from the EC2 Elastic IP;
- disabling the EC2 host, CloudFront distribution, signed-media key group,
  backup controls, termination protection, or data-volume protection;
- making either S3 bucket public, opening PostgreSQL to the internet, adding a
  NAT Gateway, or enabling Container Insights; or
- replacing the production host, network interface, Elastic IP, or encrypted
  data volume without a separately approved backup-and-restore procedure.

The `infrastructure/bootstrap` stack only owns the versioned remote-state
bucket. Do not rerun its original account bootstrap as an application
deployment procedure.

## Isolated application recovery smoke

Use only the separate `infrastructure/recovery-smoke` root and its
`recovery-terraform` wrapper. Each authorized drill needs a fresh 32-hex
operation ID and isolated state key; it must never use production state, raw
Terraform, SSH, ingress, production DNS, or production writes.

The reviewed plan may create only operation-tagged recovery resources: one
zero-ingress host/security group/role and one encrypted snapshot clone. Use the
recovery mount helper exclusively; direct mounts, `/etc/fstab`, generic repairs,
and `xfs_repair -L` are forbidden. A recognized dirty journal requires the
operation-bound `REPLAY-JOURNAL <operation-id>` flow while Docker remains
masked. Access the loopback recovery TLS listener only through SSM.

Follow [the recovery-smoke command guide](recovery-smoke/README.md) exactly and
the [recovery control sequence](../docs/backup-restore.md). Finish with the
wrapper's `DESTROY <operation-id>` confirmation and `cleanup-check`, and retain
redacted evidence.

## Build and release

The backend image must be built for ARM64, pushed to the existing ECR
repository, and pinned by digest. Tags alone are rejected by the host
deployment helper.

Connect to the host through Systems Manager Session Manager; SSH is not open.
The reviewed release configuration is `/etc/duducar/release.env`. Current
production must select:

```text
CADDY_CONFIG=/etc/duducar/Caddyfile.post-cutover
```

The same release file carries the optional DUDU-owned OTA selection:
`APP_UPDATE_VERSION_CODE`, `APP_UPDATE_VERSION_NAME`, `APP_UPDATE_STORAGE_NAME`,
`APP_UPDATE_SHA256`, `APP_UPDATE_SIZE_BYTES`, and
`APP_UPDATE_ROLLOUT_PERCENT`. Terraform defaults keep all six disabled. When
enabled, the signed APK must already exist at the private `updates/*.apk` key;
the release-config document validates the exact reviewed values and never
restarts services.

`Caddyfile.preflight` and `Caddyfile.production` are retired migration
artifacts. The checked-in runtime helper refuses to use them.

Never hand-edit or copy `/etc/duducar/release.env`. The dedicated
`production_release_config_document` is the only reviewed path to change
the backend, PostgreSQL, Caddy, Caddy-config, required-app, and Play Integrity
project selections.
Every image and the semantic Android version must exactly match the Terraform
values embedded in the document. New hosts receive that exact initial file from
reviewed bootstrap user data; later changes are atomic, root-only, backed up,
and never print configuration or restart a service. The required app version
must equal the signed APK version name. Use the pinned Terraform/SSM
`Mode=validate`, `Mode=install`, confirmation, and rollback sequence in the
[production deployment runbook](../docs/production-deployment-runbook.md#configure-and-deploy-through-ssm).
Record the full commit, document version and hash, command IDs, digest, app
version, and one 32-hex operation ID.

The mechanical release-config rollback is permitted only before any schema
migration begins and only with the same document inputs and operation ID; it
restores its matching saved file and is not a data rollback. Once
`0010_battery_backed_player_policy` is recorded, old-image compatibility is
read-only on an isolated recovery data set only. It does not restore the former
external-power policy or authorize a normal live pre-policy image rollback;
keep the released image selected and use a reviewed forward fix.

Use the host helpers rather than a legacy ECS application task:

```sh
sudo /usr/local/sbin/render-duducar-runtime-env
sudo /usr/local/sbin/duducar-stack status
sudo /usr/local/sbin/duducar-command readiness
sudo systemctl status duducar.service
sudo systemctl list-timers 'duducar-*'
```

Terraform user data is bootstrap-only and is not replayed on the live host.
All runtime scripts, units, timers, PostgreSQL access/grants, the Caddyfile,
credential broker, and backup verifier are delivered as one complete reviewed
SSM bundle. A Terraform apply updates that document but never executes it.
Resolve the
`production_runtime_asset_document` and `production_host_instance_id` outputs,
pin its exact version and AWS-generated hash, then send it with the full
40-character reviewed commit. The worktree must be clean so the commit and
embedded file hashes describe the same reviewed source. Use `Mode=validate`
first, wait for `Success`, and inspect the command output; only then repeat with
`Mode=install`. Record both command IDs. Validation may cache only the exact
digest-pinned Caddy image. Installation is retry-safe, backs up every prior file
(including absence), restores the complete bundle on failure, runs
`systemctl daemon-reload`, and deliberately does not enable or restart anything:

First, prepare one pinned operation in a Bash shell. This checks the AWS account,
the clean commit, and the complete asset manifest against the applied Terraform
outputs. Keep this shell open and record the operation ID:

```sh
set -eu
export AWS_PAGER=""

aws --version 2>&1 | grep -Eq '^aws-cli/2\.'
test "$(aws sts get-caller-identity \
  --profile dudu-production --query Account --output text)" = 173454940059
test -z "$(git status --porcelain)" || {
  echo "Refusing to label runtime assets from a dirty worktree." >&2
  exit 1
}

runtime_document=$(terraform -chdir=infrastructure/terraform output -raw \
  production_runtime_asset_document)
runtime_document_version=$(terraform -chdir=infrastructure/terraform output -raw \
  production_runtime_asset_document_version)
runtime_document_hash=$(terraform -chdir=infrastructure/terraform output -raw \
  production_runtime_asset_document_hash)
runtime_document_hash_type=$(terraform -chdir=infrastructure/terraform output -raw \
  production_runtime_asset_document_hash_type)
production_instance=$(terraform -chdir=infrastructure/terraform output -raw \
  production_host_instance_id)
reviewed_commit=$(git rev-parse HEAD)
runtime_operation_id=$(openssl rand -hex 16)
runtime_caddy_image=$(terraform -chdir=infrastructure/terraform output -raw \
  production_caddy_image)
asset_hashes=$(terraform -chdir=infrastructure/terraform output -json \
  production_runtime_asset_sha256)

test "$(sha256sum infrastructure/terraform/ec2/runtime/manage-runtime-assets |
  awk '{print $1}')" = "$(printf '%s' "$asset_hashes" | jq -er .operation_manager)"

wait_for_runtime_command() {
  command_id=$1
  deadline=$(($(date +%s) + 420))
  command_status=Pending
  while [ "$(date +%s)" -lt "$deadline" ]; do
    command_status=$(aws ssm get-command-invocation \
      --profile dudu-production \
      --region ap-southeast-5 \
      --command-id "$command_id" \
      --instance-id "$production_instance" \
      --query Status --output text 2>/dev/null || true)
    case "$command_status" in
      Success|Failed|TimedOut|Cancelled) break ;;
    esac
    sleep 5
  done
  case "$command_status" in
    Success|Failed|TimedOut|Cancelled) ;;
    *)
      echo "Runtime command exceeded its delivery and execution window; requesting cancellation." >&2
      aws ssm cancel-command \
        --profile dudu-production \
        --region ap-southeast-5 \
        --command-id "$command_id" \
        --instance-ids "$production_instance" >/dev/null || true
      cancel_deadline=$(($(date +%s) + 60))
      while [ "$(date +%s)" -lt "$cancel_deadline" ]; do
        command_status=$(aws ssm get-command-invocation \
          --profile dudu-production \
          --region ap-southeast-5 \
          --command-id "$command_id" \
          --instance-id "$production_instance" \
          --query Status --output text 2>/dev/null || true)
        case "$command_status" in
          Success|Failed|TimedOut|Cancelled) break ;;
        esac
        sleep 5
      done
      ;;
  esac
  aws ssm get-command-invocation \
    --profile dudu-production \
    --region ap-southeast-5 \
    --command-id "$command_id" \
    --instance-id "$production_instance" \
    --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' || true
  test "$command_status" = Success
}

printf 'Record these values in the production change record before continuing:\n'
printf 'runtime_document=%q\n' "$runtime_document"
printf 'runtime_document_version=%q\n' "$runtime_document_version"
printf 'runtime_document_hash=%q\n' "$runtime_document_hash"
printf 'runtime_document_hash_type=%q\n' "$runtime_document_hash_type"
printf 'production_instance=%q\n' "$production_instance"
printf 'reviewed_commit=%q\n' "$reviewed_commit"
printf 'runtime_operation_id=%q\n' "$runtime_operation_id"
printf 'runtime_caddy_image=%q\n' "$runtime_caddy_image"
terraform -chdir=infrastructure/terraform output production_runtime_asset_sha256
```

`runtime_caddy_image` must be the exact applied Terraform output; `current` and
tags are rejected. Validation may cache only that digest for offline Caddyfile
validation. If the shell is lost, restore
the recorded values verbatim; never generate a replacement operation ID for an
installation that may already have run.

Next, submit validation only. This block cannot install files:

```sh
validation_command=$(aws ssm send-command \
  --profile dudu-production \
  --region ap-southeast-5 \
  --document-name "$runtime_document" \
  --document-version "$runtime_document_version" \
  --document-hash "$runtime_document_hash" \
  --document-hash-type "$runtime_document_hash_type" \
  --instance-ids "$production_instance" \
  --timeout-seconds 60 \
  --parameters "Mode=validate,ExpectedCommit=$reviewed_commit,OperationId=$runtime_operation_id,CaddyImage=$runtime_caddy_image" \
  --query 'Command.CommandId' \
  --output text)
printf 'Validation command ID: %s\n' "$validation_command"
wait_for_runtime_command "$validation_command"
```

Stop here. Review the recorded command status, Caddy result, complete manifest,
commit, and operation ID. Only after approving that evidence,
run this separate block and type the operation-specific confirmation:

```sh
printf 'Type INSTALL %s to continue: ' "$runtime_operation_id"
read -r runtime_confirmation
test "$runtime_confirmation" = "INSTALL $runtime_operation_id"

install_command=$(aws ssm send-command \
  --profile dudu-production \
  --region ap-southeast-5 \
  --document-name "$runtime_document" \
  --document-version "$runtime_document_version" \
  --document-hash "$runtime_document_hash" \
  --document-hash-type "$runtime_document_hash_type" \
  --instance-ids "$production_instance" \
  --timeout-seconds 60 \
  --parameters "Mode=install,ExpectedCommit=$reviewed_commit,OperationId=$runtime_operation_id,CaddyImage=$runtime_caddy_image" \
  --query 'Command.CommandId' \
  --output text)
printf 'Install command ID: %s\n' "$install_command"
wait_for_runtime_command "$install_command"
```

If activation has not begun and validation/install fails, keep traffic unchanged
and use the same pinned document, commit, and operation ID to restore the
complete runtime bundle. The rollback refuses files from another operation:

```sh
printf 'Type ROLLBACK %s to continue: ' "$runtime_operation_id"
read -r runtime_confirmation
test "$runtime_confirmation" = "ROLLBACK $runtime_operation_id"

rollback_command=$(aws ssm send-command \
  --profile dudu-production \
  --region ap-southeast-5 \
  --document-name "$runtime_document" \
  --document-version "$runtime_document_version" \
  --document-hash "$runtime_document_hash" \
  --document-hash-type "$runtime_document_hash_type" \
  --instance-ids "$production_instance" \
  --timeout-seconds 60 \
  --parameters "Mode=rollback,ExpectedCommit=$reviewed_commit,OperationId=$runtime_operation_id,CaddyImage=$runtime_caddy_image" \
  --query 'Command.CommandId' \
  --output text)
printf 'Rollback command ID: %s\n' "$rollback_command"
wait_for_runtime_command "$rollback_command"
```

After runtime and release-config installation, use only
`production_release_activation_document`; never run the deploy/migrate sequence
piecemeal. Pin its output version/hash and first send `Mode=validate` with the
same commit/operation ID and exact `BackendImage`, `PostgresImage`, `CaddyImage`,
and `RequiredAppVersion` Terraform outputs. Validation asserts the installed
runtime manifest and release file and validates the current secret schema
without changing service/container state. The application secret must already
contain `WORKER_DB_PASSWORD` and
`PLAY_INTEGRITY_APP_CERTIFICATE_SHA256` (comma-separated canonical lowercase
64-hex fingerprints); no real value belongs in this repository.

Only after validation succeeds, type `ACTIVATE <operation-id>` and send a
separate `Mode=activate` command with that exact `Confirmation`. Use the default
`ActivationKind=existing`; it requires a current remote logical backup, a
completed current DLM snapshot, and passing host/public checks before shutdown.
`initial-empty` is permitted only for a genuinely empty PostgreSQL directory.
`failed-existing` is an exceptional recovery type, allowed only after retained
SSM evidence is independently reviewed to show a prior pre-deploy failure and
a fully stopped DUDU state. Its prior-operation and SSM-command fields are
operator-supplied audit correlation, not host-verifiable proof. It first
requires a fresh exact `ARM <operation-id> FROM <failed-operation-id>`
confirmation to write a root-only authorization valid for 15 minutes. A
separate fresh exact `RECOVER <operation-id> FROM <failed-operation-id>`
confirmation atomically consumes that authorization. The path rechecks the backup, disk,
memory, and snapshot gates without the impossible pre-start public probe,
verifies Operations SNS publication, and requires public HTTPS again after
start.
The document then stops timers/systemd, starts the scoped credential broker,
runs `duducar-stack deploy` with public Caddy absent, runs `migrate`,
`grant-runtime`, and `migration-check` in that order, starts systemd and timers,
and asserts readiness, running image digests, effective app version, service
state, and a new versioned remote backup. A post-shutdown failure leaves public
traffic stopped and raises the activation alert.

Do not rerun cloud-init or replace the host merely to update runtime files. Do
not copy a previous `release.env` after a runtime-asset rollback: before a schema
migration, use only the guarded release-config SSM rollback from the deployment
runbook. After
`0010_battery_backed_player_policy`, do not select a pre-policy backend image;
the safe live recovery path is the released image or a reviewed forward fix.

An application release requires a current logical backup, digest-pinned image
review, backward-compatible migration plan, readiness check, and rollback
evidence. The supported managed command aliases are `health`, `playlists`,
`media-reconcile`, `retention`, `backup`, `migrate`, `migration-check`,
`grant-runtime`, and `readiness`. Schema-owner commands require an explicit
review; do not improvise database commands against the live container.

Create the one-time initial owner only while bootstrapping a new, empty
database. Do not run this on the current production database: it intentionally
fails once any user exists. Use the existing account owner after the documented
owner-survival preflight instead. For a new empty deployment, use an audited
interactive Session Manager session on the production host:

```sh
sudo docker exec -it duducar-web \
  python manage.py create_initial_owner --email OWNER@duducar.co
```

Enter the password only at the hidden prompt. Never use a command-line password
argument or place it in shell history.

## Media processing and schedules

The Django host dispatches the `-ec2-media-worker` Fargate task definition for
one quarantined media asset at a time. The task uses a dedicated security group
and connects over TLS to the EC2-hosted PostgreSQL role. It is not a continuous
worker and must not be repurposed for arbitrary management commands. Dispatch
is serialized through PostgreSQL, capped at two active tasks and six `RunTask`
calls per hour (including failed or ambiguous calls), and leaves capacity-
deferred uploads quarantined for the reconciliation timer to retry without
consuming a failure attempt. Visible tasks are matched to recent reservations
by asset UUID so the two capacity signals do not count one worker twice; the
two-minute reconciliation timer picks up remaining queued uploads. An ambiguous
`RunTask` result reuses its exact
client token and attempt for at most 15 minutes; after that, reconciliation
consumes a new bounded attempt and token. Each media
command has a 20-minute ceiling in addition to the ClamAV and FFmpeg/FFprobe
subprocess ceilings, so a stuck task cannot accrue indefinite Fargate duration.
Both the shell entrypoint and Django production-readiness check refuse a worker
ceiling that is not shorter than the database processing lease.

Accepted pilot ceiling: the task role is prefix-scoped but can read every
object under shared `quarantine/` and can read/write/delete every object under
`validated/`, and its
TCP/443 rule still permits arbitrary HTTPS destinations. This is reduced
privilege, not complete per-asset or egress isolation. PostgreSQL permits only
`SELECT` plus the processing columns on `signage_mediaasset`; it cannot use the
web role or deletion outbox. Terraform exclusively owns the four worker egress
rules so the AWS default or an out-of-band allow-all rule is removed on apply.

Closing this ceiling is a separate architecture and cost decision, not a
security-group edit. Fargate image pulls, secret injection, and logs need ECR
API/DKR, Secrets Manager, CloudWatch Logs, and S3 private endpoints before the
public IP and arbitrary HTTPS rule can be removed. Fresh ClamAV definitions
then need a monitored allowlisted proxy or a separate trusted publisher, and
DNS exfiltration needs an approved VPC-wide Resolver DNS Firewall policy or a
separate worker VPC. Exact object isolation requires the web tier to reserve
the attempt/output identity before dispatch and issue an opaque one-time
capability (or equally protected exact object credentials), after which the
task role's S3/KMS access can be removed; database mediation or row-level
authorization is also required for complete per-asset isolation. Approve the
endpoint/proxy or publisher/VPC design, recurring cost, and backend capability
protocol together before implementing it.

Application schedules run as local systemd timers, not EventBridge rules.
The 15-minute reconciliation unit runs both processing reconciliation and
`reconcile_media_deletions --limit 25`, so durable deletion-outbox retries do
not depend on a dashboard request.
Inspect failures and recovery notifications with:

```sh
sudo journalctl -u 'duducar-command@*.service'
sudo journalctl -u duducar.service
```

Historical application, scheduled, rollback, and polling-worker task-definition
revisions were deregistered after dependency review. No arbitrary ECS
management-command wrapper remains; current managed commands run through an
audited Session Manager session and `/usr/local/sbin/duducar-command`.

## Backup, recovery, and rollback

The current database is PostgreSQL on encrypted EC2 data storage. RDS snapshots
contain only stale historical data. Create logical backups through the managed
command:

```sh
sudo /usr/local/sbin/duducar-command backup
```

Success requires both archive and sidecar S3 HEAD responses to contain the
expected SHA-256 checksum, KMS encryption, size, metadata, and concrete version
IDs; the verifier then downloads that exact sidecar version and atomically
writes a root-only receipt. Host health rechecks the receipt, exact object
versions, root/data disk, inodes, memory, a completed data-volume snapshot
newer than 36 hours, and both public readiness paths. Any failed check or SNS
publish now fails the systemd command instead of silently reporting success.

DLM snapshots supplement, but never replace, logical backups and restore tests.
Two AWS-native `AWS/EBS` alarms monitor the exact Terraform-managed DLM policy
without depending on the production host. `SnapshotsCreateFailed` alarms after
DLM exhausts its create retries. `SnapshotsCreateCompleted` is sparse, so the
freshness alarm converts missing hours to zero and requires one completion in
the latest 36 hourly periods; a new or genuinely stale policy remains in alarm
until its first successful run. The host check still independently verifies
that the newest completed snapshot belongs to the exact source volume and has
the expected tags. Operators must require both layers before every release.
Current restore and rollback rules are in
[`docs/backup-restore.md`](../docs/backup-restore.md).

## Host patching and observability

Bootstrap applies available Amazon Linux security updates once. Ongoing patches
require a reviewed maintenance window through SSM: preview `dnf upgrade
--security --assumeno`, record the package/kernel delta, take and verify the
logical backup plus DLM snapshot, apply `dnf upgrade --security -y`, reboot only
when required, and then require SSM connectivity, `duducar.service`, every
timer, `duducar-stack assert-release`, readiness, host health, and a fresh
remote backup. Do not enable unattended rebooting on the single-host pilot.

Use `journalctl` through Session Manager for host/broker/stack/timer evidence;
journald is capped, compressed, and sealed. EC2 status, CPU, CPU-credit, worker
failure, host-health, backup, snapshot, and budget notifications use the
operations SNS topic. Accepted cost ceiling: host journals are not shipped to a
second system, so a lost root volume can lose detailed logs. CloudWatch Agent or
another centralized journal sink is the upgrade path before a larger rollout.
