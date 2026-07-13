# Production Infrastructure Deployment

These commands are owner-run. They use the currently authenticated AWS identity
and never require AWS credentials in this repository. Run them from a clean,
reviewed release commit.

## Prerequisites

- AWS CLI authenticated with MFA-backed temporary credentials.
- Terraform 1.10 or newer, Docker Buildx, and `jq`.
- Route 53 hosted zone for `duducaradmin.com` in the production account.
- Company SMTP non-secret settings and Google Cloud project number.
- Application secrets ready for direct entry in Secrets Manager.
- A completed and approved `docs/aws-cost-estimate.md` worksheet.

## 1. Bootstrap remote Terraform state

```sh
terraform -chdir=infrastructure/bootstrap init
terraform -chdir=infrastructure/bootstrap plan -out=bootstrap.tfplan
terraform -chdir=infrastructure/bootstrap apply bootstrap.tfplan
terraform -chdir=infrastructure/bootstrap output -raw state_bucket
```

Copy `infrastructure/terraform/backend.hcl.example` to an untracked
`backend.hcl`, replace the account number with the output, and confirm
`git status` does not list the file.

## 2. Provision foundations with services disabled

Copy `terraform.tfvars.example` to the gitignored `terraform.tfvars`, enter only
non-secret values, and leave `enable_services=false` and `container_image=""`.

```sh
terraform -chdir=infrastructure/terraform init -backend-config=backend.hcl
terraform -chdir=infrastructure/terraform fmt -check
terraform -chdir=infrastructure/terraform validate
terraform -chdir=infrastructure/terraform plan -out=foundation.tfplan
terraform -chdir=infrastructure/terraform apply foundation.tfplan
```

Review every plan. Stop if it creates resources outside `ap-southeast-5`, makes
either S3 bucket public, makes RDS public, adds a NAT Gateway, or exceeds the
approved cost model.

## 3. Populate the application secret

Open the application secret ARN from Terraform output in AWS Secrets Manager.
Create one JSON secret value containing these exact keys:

- `DJANGO_SECRET_KEY`: at least 50 random characters.
- `EMAIL_HOST_USER`: dedicated company SMTP username.
- `EMAIL_HOST_PASSWORD`: dedicated company SMTP app credential.
- `PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON`: the complete Google service-account
  JSON encoded as a JSON string value.

Do not place this JSON in the repository, Terraform inputs, terminal history,
support tickets, or chat. Confirm the SNS subscription email sent to the
operations address.

## 4. Build and push an immutable ARM64 image

```sh
export AWS_REGION=ap-southeast-5
export ECR_REPOSITORY=$(terraform -chdir=infrastructure/terraform output -raw ecr_repository_url)
export RELEASE_TAG=$(git rev-parse --verify HEAD)
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${ECR_REPOSITORY%%/*}"
docker buildx build --platform linux/arm64 --tag "$ECR_REPOSITORY:$RELEASE_TAG" --push backend
```

Set `container_image` in the untracked tfvars file to that immutable URI, keep
services disabled, then plan and apply again. This creates the one-off task
definition without starting the public service.

## 5. Migrate and validate before starting services

```sh
./scripts/run-production-task.sh python manage.py migrate --noinput
./scripts/run-production-task.sh python manage.py check_deployment_readiness --environment production
```

## 6. Start and verify services

Set `enable_services=true`, review the plan, and apply. Then verify:

```sh
curl --fail https://marketing.duducaradmin.com/health/live/
curl --fail https://api.marketing.duducaradmin.com/health/ready/
```

Find the running web task in ECS and create the initial owner through an
interactive ECS Exec session:

```sh
aws ecs execute-command \
  --region ap-southeast-5 \
  --cluster duducar-signage-production \
  --task RUNNING_WEB_TASK_ARN \
  --container application \
  --interactive \
  --command "python manage.py create_initial_owner --email OWNER@duducar.co"
```

The command prompts twice without echoing the password. Do not use the
`--password` option in production or place the password in shell history.

Confirm one healthy ALB target, one web task, one worker task, scheduled rules,
current ClamAV definitions in worker logs, password-reset delivery, private S3
URLs, and all CloudWatch/SNS alarms. Upload only the three rehearsal assets
until media processing is proven.

## Rollback

For a web regression, select the preceding ECS task-definition revision and
force a new deployment. Stop the worker before rollback if media processing is
implicated. Never reverse a database migration without a reviewed restore plan.
Take a manual RDS snapshot before any non-backward-compatible migration.

For disaster recovery, restore RDS to an isolated instance, recover the matching
S3 object version, run production readiness against the isolated endpoints, and
record elapsed recovery time. Do not point production DNS at a restore until the
owner approves the evidence.
