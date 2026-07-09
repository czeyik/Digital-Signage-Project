# Production Deployment Runbook And IaC Plan

This runbook prepares the pilot for an end-to-end production rehearsal. It is a
deployment plan, not a command to create live infrastructure. Keep development
and production completely separate.

## Source References

- AWS regional services list: https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/
- ECS overview: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/Welcome.html
- ECS with Application Load Balancer: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/alb.html
- EventBridge Scheduler for ECS tasks: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/tasks-scheduled-eventbridge-scheduler.html
- RDS automated backups: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.html
- S3 Block Public Access: https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html
- SES endpoints: https://docs.aws.amazon.com/general/latest/gr/ses.html

## Region Decision

Evaluate `ap-southeast-5` Malaysia first. AWS documents core services included
in all Region launches, including ECS, Fargate, ECR, RDS, S3, Secrets Manager,
KMS, CloudWatch, EventBridge, Route 53, and ACM. If pricing, quotas, SES SMTP,
or support constraints make Malaysia unsuitable, use `ap-southeast-1`
Singapore and document the reason.

SES API endpoints are available in Malaysia. SES SMTP endpoints are not
currently available in Malaysia, so this Django deployment should either:

- use SES SMTP in Singapore, with `EMAIL_HOST=email-smtp.ap-southeast-1.amazonaws.com`;
- or add a production SES API email backend before launch.

The current code supports SMTP through environment variables.

## Required Accounts And Access

- AWS production account, separate from any development account.
- IAM access for one deployer, protected by MFA outside the application.
- ECR repository access for backend image pushes.
- Route 53 or registrar access for `duducaradmin.com`.
- SES sender identity for `duducar.co` or a verified sending subdomain.
- Owner dashboard account created by the one-time deployment command.
- Mobile data/SIM accounts for each pilot tablet.
- Internal storage for hardware evidence photos, logs, and notes.

## Production Services

- VPC with at least two public subnets and two private subnets.
- Application Load Balancer with HTTP to HTTPS redirect.
- ACM certificate covering:
  - `marketing.duducaradmin.com`
  - `api.marketing.duducaradmin.com`
- ECS Fargate cluster.
- ECS service for the Django web/API container.
- ECS service or scheduled task for `python manage.py process_media --loop`.
- EventBridge Scheduler tasks for maintenance commands.
- RDS PostgreSQL 16 with encryption, automated backups, and deletion
  protection.
- Private S3 media bucket with all Block Public Access settings enabled.
- KMS keys for RDS, S3, logs, and secrets where suitable.
- Secrets Manager entries for application secrets.
- CloudWatch log groups and alarms.
- Optional AWS Backup plan if budget allows.

## IaC Resource Plan

Model the infrastructure in Terraform, AWS CDK, or CloudFormation. Keep one
state/stack per environment.

Recommended modules or stacks:

- `network`: VPC, subnets, route tables, NAT decision, security groups.
- `security`: KMS keys, IAM roles, least-privilege policies.
- `database`: RDS PostgreSQL parameter group, subnet group, instance, backups.
- `storage`: private media bucket, versioning, lifecycle rules, bucket policy.
- `containers`: ECR repository, ECS cluster, task definitions, services.
- `load_balancer`: ALB, target groups, listeners, ACM, health checks.
- `scheduled_tasks`: EventBridge Scheduler rules for Django management commands.
- `dns`: Route 53 records for dashboard and API hostnames.
- `observability`: CloudWatch logs, metric filters, alarms, dashboards.

The web task and worker task should use the same image but different commands:

```sh
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 60
python manage.py process_media --loop
```

Scheduled task commands:

```sh
python manage.py evaluate_device_health
python manage.py apply_retention
python manage.py create_pilot_backup --output-dir /secure/signage-backups --skip-media
```

Use managed RDS and S3 backups as the primary recovery path. The app backup is
an additional readability check and fixture-style export, not a replacement for
RDS point-in-time recovery or S3 object protection.

## Environment Variables

Required production values:

```env
DJANGO_DEBUG=false
DEPLOYMENT_ENV=production
DJANGO_SECRET_KEY=<Secrets Manager value>
DJANGO_ALLOWED_HOSTS=marketing.duducaradmin.com,api.marketing.duducaradmin.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://marketing.duducaradmin.com
DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_TRUST_X_FORWARDED_PROTO=true
DJANGO_USE_X_FORWARDED_HOST=false
DATABASE_URL=postgresql://<user>:<password>@<rds-endpoint>:5432/signage
DB_SSLMODE=require
AWS_STORAGE_BUCKET_NAME=<private media bucket>
AWS_S3_REGION_NAME=ap-southeast-5
REQUIRED_APP_VERSION=0.1.0
DEFAULT_FROM_EMAIL=no-reply@duducar.co
SERVER_EMAIL=no-reply@duducar.co
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=email-smtp.ap-southeast-1.amazonaws.com
EMAIL_PORT=587
EMAIL_HOST_USER=<SES SMTP username>
EMAIL_HOST_PASSWORD=<SES SMTP password>
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
PILOT_BACKUP_RETENTION_DAYS=30
```

Never commit production `.env` files or secrets.

## Deployment Sequence

1. Create AWS account guardrails, budgets, and deployment IAM.
2. Create DNS hosted zone or obtain registrar access.
3. Provision network, KMS, S3, RDS, Secrets Manager, ECR, ECS, ALB, and
   CloudWatch through IaC.
4. Verify SES identity and move SES out of sandbox if needed.
5. Build and push the backend image.
6. Run migrations as a one-off ECS task:

   ```sh
   python manage.py migrate --noinput
   ```

7. Run deployment readiness as a one-off ECS task:

   ```sh
   python manage.py check_deployment_readiness --environment production
   ```

8. Create the initial owner:

   ```sh
   python manage.py create_initial_owner --email owner@duducar.co --password '<temporary-strong-password>'
   ```

9. Start the web service and confirm ALB health checks pass.
10. Start the media worker.
11. Create EventBridge Scheduler jobs for health evaluation, retention, and
    backups.
12. Log in to the dashboard, change the initial password, create pilot users,
    and confirm password reset email delivery.

## End-To-End Rehearsal

Run this before any passenger-facing pilot:

1. Upload one JPEG, one PNG, and one MP4.
2. Confirm uploads enter quarantine.
3. Confirm the media worker scans, validates, normalizes, removes audio from
   MP4, and marks assets ready.
4. Confirm media preview works through signed private S3 URLs.
5. Create and publish a weekly playlist.
6. Create one device, vehicle, and driver assignment as the owner.
7. Issue a one-time enrollment code.
8. Install a release-signed APK on qualified Android 12+ hardware.
9. Enroll the device within 15 minutes.
10. Confirm the device downloads the manifest and media from production.
11. Play one full loop on external power.
12. Confirm heartbeat, sync time, last playback, and proof-of-play arrive.
13. Disconnect mobile data during sync and event upload, then reconnect.
14. Confirm queued events upload once connectivity returns.
15. Disconnect external power mid-item, restart, and confirm an interrupted
    result is uploaded without duplicate contractual plays.
16. Disable the device in the dashboard and confirm maintenance mode.
17. Reactivate explicitly and confirm playback resumes only after sync.
18. Run `evaluate_device_health` and confirm expected alerts.
19. Export CSV and confirm driver names are not included.
20. Create and verify a backup; document restore steps from RDS and S3.

## Hardware Launch Gate

Do not launch until the exact tablet model and firmware has a
`HardwareQualification` record with every required test passed and an evidence
reference. Emulator and phone testing are development checks only.

Minimum hardware evidence:

- Device-owner provisioning and lock-task mode after factory reset.
- Automatic start on vehicle power.
- Reliable external-power behavior for the selected hardware.
- 12-hour playback stability with 1080p H.264 media.
- Mobile data reconnect after weak-signal and tunnel tests.
- Thermal behavior in representative vehicle conditions.
- Approved mount, cable, fuse, and power adapter.

## Rollback And Recovery

- Keep the previous ECS task definition active for rollback.
- Database migrations must be backward-compatible during the pilot. If a
  migration cannot be backward-compatible, schedule downtime and take a manual
  RDS snapshot first.
- For web regressions, roll the ECS service back to the previous task
  definition.
- For media processing failures, stop the media worker, keep quarantined assets
  unpublished, and continue serving the last valid playlist.
- For Android regressions, stop sideloading the new APK. Remote application
  updates are out of scope; staff must manually reinstall a known-good APK.
- Restore drills must prove RDS snapshot restore, S3 object availability, and
  dashboard login before the pilot depends on backups.

## Launch Acceptance

Launch only when all of these are true:

- Production readiness command passes in production.
- Backend tests and Android compile pass from a clean checkout.
- Password reset email works.
- Media worker has processed real test files from private object storage.
- Device enrollment, sync, playback, heartbeat, proof upload, offline queueing,
  disablement, and reactivation all work against production.
- Backup creation and restore verification are documented.
- Hardware qualification is complete for the exact production hardware.
- Monthly AWS cost estimate is documented and stays within the RM500 pilot
  target excluding tablet mobile-data allowances.
