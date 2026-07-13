# AWS Pilot Cost Approval Worksheet

Status: **not approved until an AWS Pricing Calculator export is attached**.

Terraform intentionally provisions pilot capacity only in `ap-southeast-5` and
does not create a NAT Gateway. Use the exact Terraform inputs below in AWS
Pricing Calculator; do not substitute Singapore or x86 prices.

| Service | Production sizing to enter |
| --- | --- |
| ECS Fargate web | Linux/ARM, 0.5 vCPU, 1 GB, one task, 730 hours/month |
| ECS Fargate media worker | Linux/ARM, 1 vCPU, 2 GB, one task, 730 hours/month |
| ECS scheduled tasks | Linux/ARM, 0.5 vCPU, 1 GB, allow 10 task-hours/month |
| RDS PostgreSQL | Single-AZ `db.t4g.micro`, PostgreSQL 16, 730 hours/month |
| RDS storage | 20 GB GP3, 30-day automated backups, allow growth to 50 GB |
| Application Load Balancer | One ALB, 730 hours, one low-traffic LCU estimate |
| Public IPv4 | ALB addresses plus one web and one worker task address |
| S3 Standard | Start at 25 GB media/backups; include versioned objects and requests |
| ECR | 10 GB of ARM64 images |
| CloudWatch | 5 GB logs/month, 30-day retention, Container Insights and alarms |
| KMS, Secrets Manager, Route 53 | One customer key, two managed secrets, hosted-zone queries |
| Internet transfer | Enter the expected tablet media downloads separately |

Record the calculator share link/export date, USD subtotal, exchange rate, RM
subtotal, tax treatment, and a 15% usage contingency below:

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

The approval total must remain at or below RM500/month, excluding the RM40
mobile-data allowance per tablet. If it does not, stop before `terraform apply`.
The owner must also set `monthly_budget_usd` in the untracked Terraform variables
to the current USD equivalent of RM500; Terraform creates forecast notifications
at 80%, 90%, and 100%, plus actual notifications at 90% and 100%.

Pricing is usage- and date-dependent. Re-run this worksheet immediately before
deployment using the official [AWS Pricing Calculator](https://calculator.aws/),
[Fargate pricing](https://aws.amazon.com/fargate/pricing/), and
[RDS pricing](https://aws.amazon.com/rds/postgresql/pricing/).
