# AWS Pilot Cost Worksheet

## Baseline

The 10-device production pilot in `ap-southeast-5` uses:

| Service | Sizing assumption |
| --- | --- |
| EC2 | One Linux/ARM `t4g.small`, 730 hours/month, standard CPU credits |
| EBS | Encrypted 8-GiB root and 32-GiB data volumes |
| Network | One attached Elastic IP; normal Route 53 queries |
| Backups | Daily versioned S3 archive and 30 incremental DLM recovery points |
| Processing | One 1-vCPU/2-GiB ARM Fargate task only when an upload needs it |
| Media | Private S3, CloudFront OAC/signed URLs, request and egress charges |
| Operations | One KMS key, one application secret, ECR, 30-day logs, SNS, and standard alarms |

The working steady-state estimate is **USD 25–28 per month including Malaysian
service tax**, assuming pilot traffic, modest media storage/egress, and short
processing bursts. Recalculate when instance size, retention, storage, egress,
or worker execution changes.

The 2026-07-28 migration removed the recurring ALB, live RDS, continuous ECS
web/worker services, application schedules, and Container Insights. See the
[migration record](archive/2026-07-28-usd30-migration.md) for historical
evidence; migration-month overlap is not a steady-state measurement.

## Temporary retained storage

- One encrypted 20-GiB final RDS snapshot contains stale pre-cutover data. Its
  `ReviewAfter` tag is `2026-08-27`; deletion requires current logical/DLM
  restore evidence and a reviewed cleanup.
- One encrypted 32-GiB manual bootstrap snapshot remained after the first DLM
  snapshot completed. Retain its incremental cost until the exact DLM recovery
  point passes the isolated restore gate and a plan identifies only that manual
  snapshot for deletion.

Neither snapshot is a current rollback path.

## Budget and measurement

Terraform maintains a `Project=duducar-signage` USD 30 monthly budget with
actual alerts at 60%, 80%, 90%, and 100%, plus an 80% forecast alert. Keep the
account-wide USD 115 guard because tax and unallocated/shared charges may not
appear in the project filter.

Tags, Cost Explorer, and forecasts are delayed; USD 0 or no forecast does not
mean the stack is free. Review project and account totals after material
changes, weekly during the pilot, and at month end. Include tax, snapshot
growth, S3/CloudFront egress, Fargate duration, ECR, and shared free-tier use.

## Approval record

Attach an official AWS Pricing Calculator export using Malaysia prices and
record:

```text
Calculator link or export:
Price date:
USD subtotal:
MYR/USD exchange rate and source:
RM subtotal before tax:
15% contingency:
Estimated RM total:
Approved by / date:
```

The approval gate is **USD 30 per month**, excluding each tablet's RM 40 mobile
data allowance. The owner must explicitly approve a changed target.
