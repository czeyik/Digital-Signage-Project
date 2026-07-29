# AWS Pilot Cost Worksheet

## Current production baseline

The USD 30 migration completed on 2026-07-28 in `ap-southeast-5`. Production
now uses the following pilot-scale resources:

| Service | Current sizing and charging assumption |
| --- | --- |
| EC2 | One Linux/ARM `t4g.small`, 730 hours/month, standard CPU credits |
| EBS | 8 GB encrypted GP3 root plus 32 GB encrypted GP3 data |
| Public IPv4 | One Elastic IP attached to the running instance |
| Database backups | Daily logical S3 backup plus a DLM policy scheduled for daily incremental EBS snapshots; both target 30-day retention |
| Media processing | No continuous worker; one ARM Fargate task at 1 vCPU/2 GB only when an upload needs processing |
| Media delivery | Private S3, CloudFront OAC, signed URLs, and normal request/egress charges |
| Operations | One KMS key, one application secret, ECR, 30-day application logs, SNS, Route 53 queries, and standard alarms |

The expected steady-state project run rate is approximately **USD 25–28 per
month including Malaysian service tax**, assuming pilot traffic, modest media
storage/egress, and short Fargate processing bursts. Recalculate if downloads,
media storage, or processing time materially increase.

## Historical decommissioned cost drivers — 2026-07-28

The completed migration removed these recurring legacy resources:

- the Application Load Balancer and its public addresses;
- the live RDS PostgreSQL instance;
- the continuous ECS web and media-worker services;
- Fargate-based schedules and schedule alarms; and
- paid ECS Container Insights.

One encrypted 20 GB final RDS snapshot is retained temporarily for historical
recovery review. Its `ReviewAfter` tag is `2026-08-27`; the tag does not delete
it automatically. Delete it after that date only if the current EC2 logical
backups, DLM snapshots, and restore evidence remain valid. It contains stale
pre-cutover data and is not a direct rollback path.

A separate encrypted 32 GB manual EBS bootstrap snapshot had
`ReviewAfter=2026-07-29` while the first DLM run was pending. The live review on
2026-07-30 confirmed that it still exists and that the first complete,
less-than-24-hour-old DLM snapshot is available. Continue including the manual
snapshot's incremental storage until a restore of the exact DLM recovery point
passes and a reviewed cleanup removes only the manual snapshot.

## Budget and measurement

Terraform maintains a monthly USD 30 budget filtered to
`Project=duducar-signage`, with actual notifications at 60%, 80%, 90%, and
100%, plus a forecast notification at 80%.

AWS cost-allocation tags and Cost Explorer are delayed. Immediately after the
migration the tagged budget may show USD 0 and no forecast; that is not proof
that the architecture is free. Check:

1. after 48 hours, for the first tagged usage;
2. after 7 and 14 days, for a projected monthly run rate; and
3. at month end, including tax, snapshot storage, CloudFront/S3 egress, and
   on-demand worker duration.

The USD 30 target applies to this project. Other workloads in the same AWS
account remain outside this tag-filtered budget and must be measured
separately.

### Point-in-time budget check — 2026-07-30

The project-tagged budget reported USD 1.074 month-to-date and no forecast. The
shared account-wide guard reported USD 91.221 actual and USD 98.680 forecast,
including unrelated workloads. July still contains the retired RDS, ALB, and
continuous ECS overlap, so neither number is a steady-state validation of the
USD 25–28 estimate. Continue the 7-day, 14-day, and first-full-month checks.

### Historical budget measurement — 2026-07-28

At the migration handoff, the account-wide budget reported USD 86.903
month-to-date and a USD 102.242 forecast, while the newly tagged project
budget still reported USD 0 with no forecast. Retain the account-wide alert
until the project budget begins reporting tagged usage.

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

The approval gate for this AWS project is **USD 30 per month**, excluding each
tablet's RM 40 monthly mobile-data allowance. Re-run the estimate before
changing the instance type, retention, storage, egress, or worker execution
model.
