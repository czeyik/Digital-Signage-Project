# USD 30 Production Infrastructure Operations

The live production topology is:

- one ARM64 `t4g.small` EC2 host with an Elastic IP;
- Caddy, Django, and local PostgreSQL containers managed by systemd;
- Route 53 application records pointing directly to the EC2 Elastic IP;
- private S3 media objects delivered through CloudFront signed URLs;
- one isolated Fargate media-worker task started on demand for each upload;
- local systemd timers for health, playlists, media reconciliation, retention,
  and logical backups; and
- encrypted EBS storage, daily logical S3 backups, and DLM snapshots.

There is no live ECS web service, EventBridge application schedule, ALB, RDS
instance, continuous media worker, or ECS Container Insights. The ECS cluster
remains only because the application dispatches isolated Fargate media tasks.

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

`Caddyfile.preflight` and `Caddyfile.production` are retired migration
artifacts. The checked-in runtime helper refuses to use them.

Use the host helpers rather than a legacy ECS application task:

```sh
sudo /usr/local/sbin/render-duducar-runtime-env
sudo /usr/local/sbin/duducar-stack status
sudo /usr/local/sbin/duducar-command readiness
sudo systemctl status duducar.service
sudo systemctl list-timers 'duducar-*'
```

Terraform user data is bootstrap-only and is not replayed on the live host.
When either `Caddyfile.post-cutover` or `render-runtime-env` changes, the
reviewed plan updates an SSM command document without executing it. Resolve the
`production_runtime_asset_document` and `production_host_instance_id` outputs,
pin its exact version and AWS-generated hash, then send it with the full
40-character reviewed commit. The worktree must be clean so the commit and
embedded file hashes describe the same reviewed source. Use `Mode=validate`
first, wait for `Success`, and inspect the command output; only then repeat with
`Mode=install`. Record both command IDs. The install mode is retry-safe, backs
up both prior files before using same-directory atomic replacements, restores
both on an installation failure, and deliberately does not restart the stack:

First, prepare one pinned operation in a Bash shell. This checks the AWS account,
the clean commit, and all three local asset hashes against the applied Terraform
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
runtime_caddy_image=current
asset_hashes=$(terraform -chdir=infrastructure/terraform output -json \
  production_runtime_asset_sha256)

test "$(sha256sum infrastructure/terraform/ec2/runtime/Caddyfile.post-cutover |
  awk '{print $1}')" = "$(printf '%s' "$asset_hashes" | jq -er .caddyfile)"
test "$(sha256sum infrastructure/terraform/ec2/runtime/render-runtime-env |
  awk '{print $1}')" = "$(printf '%s' "$asset_hashes" | jq -er .environment_render)"
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

Leave `runtime_caddy_image=current` when the release keeps the running Caddy
image. If the release changes Caddy, pull the reviewed digest on the host first,
set `runtime_caddy_image` to that full digest, and record it. Validation never
pulls an image and refuses a tag-only reference. If the shell is lost, restore
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

Stop here. Review the recorded command status, Caddy result, three staged
SHA-256 values, commit, and operation ID. Only after approving that evidence,
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

If rendering, deployment, or readiness later fails, keep traffic unchanged and
use the same pinned document, commit, and operation ID to restore both runtime
files. The rollback refuses to overwrite files from a different operation:

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

After the validated installation, render the runtime environment and use the
normal maintenance-window deployment. Do not rerun cloud-init or replace the
host merely to update these files. After a runtime-asset rollback, restore the
previous reviewed `release.env`, render with the restored script, and run the
normal previous-image deployment and readiness checks.

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
consuming a failure attempt. An ambiguous `RunTask` result reuses its exact
client token and attempt for at most 15 minutes; after that, reconciliation
consumes a new bounded attempt and token. Each media
command has a 20-minute ceiling in addition to the ClamAV and FFmpeg/FFprobe
subprocess ceilings, so a stuck task cannot accrue indefinite Fargate duration.
Both the shell entrypoint and Django production-readiness check refuse a worker
ceiling that is not shorter than the database processing lease.

Application schedules run as local systemd timers, not EventBridge rules.
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

The current database is local PostgreSQL on the encrypted EC2 data volume.
RDS snapshots are historical decommission evidence and do not contain current
production writes.

Use the current logical backup command and verify its S3 archive and checksum:

```sh
sudo /usr/local/sbin/duducar-command backup
```

Daily DLM snapshots are a second, crash-consistent recovery layer; they do not
replace logical backups or restore tests. Restore into an isolated database or
volume, reconcile immutable record counts, and record RPO/RTO evidence before
relying on a backup.

A rollback is a host image/configuration rollback plus a reviewed database
recovery decision. It is never performed by enabling the retired ALB, ECS web
service, schedules, or RDS controls.

The completed migration evidence is archived in
[`docs/archive/2026-07-28-usd30-migration.md`](../docs/archive/2026-07-28-usd30-migration.md).
Current recovery procedures remain in
[`docs/backup-restore.md`](../docs/backup-restore.md).
