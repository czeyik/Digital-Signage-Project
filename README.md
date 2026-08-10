# DUDU Car Digital Signage

Production-oriented pilot platform for the DUDU Car in-vehicle signage service:

- Django dashboard and device API in `backend/`
- Android 12+ locked-player application in `android-player/`
- AWS Malaysia Terraform in `infrastructure/`
- Production, release, backup, and hardware procedures in `docs/`

Current production uses the pilot-scale AWS Malaysia design in the
[architecture](docs/architecture.md) and targets USD 30 per month. Start live
work with the [current handoff](HANDOFF.md) and
[production runbook](docs/production-deployment-runbook.md); use the
[infrastructure guide](infrastructure/README.md) for operator commands and the
[recovery runbook](docs/backup-restore.md) for backup/restore controls.

Do not enroll a production tablet until its exact model and firmware passes
[hardware qualification](docs/hardware-qualification.md), device integrity, and
every runbook gate. Review the [cost worksheet](docs/aws-cost-estimate.md)
before any infrastructure change that could affect the target.

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
