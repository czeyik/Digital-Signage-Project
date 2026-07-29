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

An application release requires a current logical backup, digest-pinned image
review, backward-compatible migration plan, readiness check, and rollback
evidence. The supported managed command aliases are `health`, `playlists`,
`media-reconcile`, `retention`, `backup`, `migrate`, `migration-check`,
`grant-runtime`, and `readiness`. Schema-owner commands require an explicit
review; do not improvise database commands against the live container.

To create the one-time initial owner, use an audited interactive Session
Manager session on the production host:

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
worker and must not be repurposed for arbitrary management commands.

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
