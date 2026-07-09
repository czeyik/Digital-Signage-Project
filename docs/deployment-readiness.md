# Deployment Readiness

Run the readiness check before deploying production:

```sh
python manage.py check_deployment_readiness --environment production
```

The production check verifies that debug mode is off, PostgreSQL is configured,
private object storage is configured, production hostnames are present, secure
cookies, HTTPS redirect, trusted proxy HTTPS detection, SMTP email, and media
processing tools are available. Console-only email is a production error because
password reset must work before launch.

The production web service should run the container default Gunicorn command.
The media worker must run separately:

```sh
python manage.py process_media --loop
```

Scheduled production tasks should run:

```sh
python manage.py evaluate_device_health
python manage.py apply_retention
python manage.py create_pilot_backup --output-dir /secure/signage-backups --skip-media
```

Development and production must use separate databases, buckets, secrets,
credentials, enrollment codes, backup roots, and device identities. Set
`DEPLOYMENT_ENV` explicitly in each environment and never reuse production
credentials locally.

See `docs/production-deployment-runbook.md` for the production infrastructure
plan and end-to-end launch rehearsal.
