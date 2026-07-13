# DUDU Car Digital Signage

Production-oriented pilot platform for the DUDU Car in-vehicle signage service:

- Django dashboard and device API in `backend/`
- Android 12+ locked-player application in `android-player/`
- AWS Malaysia Terraform in `infrastructure/`
- Production, release, backup, and hardware procedures in `docs/`

Start with [the production runbook](docs/production-deployment-runbook.md). The
AWS owner-run commands and secret-handling sequence are in
[the infrastructure guide](infrastructure/README.md). Do not enroll a production
tablet until its exact model and firmware has an approved hardware qualification
record and all go/no-go gates in the runbook pass.

The owner must complete [the AWS cost worksheet](docs/aws-cost-estimate.md)
before applying infrastructure.

## Local verification

```sh
cd backend
../.venv/bin/ruff check .
../.venv/bin/python manage.py check
../.venv/bin/python manage.py makemigrations --check --dry-run
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 DJANGO_SETTINGS_MODULE=config.settings \
  ../.venv/bin/pytest -p pytest_django.plugin
../.venv/bin/python manage.py check_deployment_readiness --environment development
```

Android and Terraform verification are also run by `.github/workflows/ci.yml`.
Production credentials, Android keystores, Terraform state, plans, and secret
environment files must never be committed.
