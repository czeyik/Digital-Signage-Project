# Production Deployment Readiness

Production currently runs on one ARM `t4g.small`: Caddy fronts the
Django/Gunicorn container, PostgreSQL 16 runs on the encrypted local data
volume, validated media is delivered from private S3 through signed CloudFront
URLs, and each upload may dispatch one isolated Fargate worker task. There is
no ECS web service, continuous media worker, ALB, or live RDS instance.

## Application readiness

Run the production check in a production-like environment before building the
release image:

```sh
python manage.py check_deployment_readiness --environment production
```

The check verifies that debug mode is off, PostgreSQL and private object
storage are configured, production hostnames are present, secure cookies,
HTTPS redirect, trusted proxy HTTPS detection, SMTP email, and media-processing
tools are available. Console-only email is a production error because password
reset must work before launch.

After deploying the pinned image, connect to the EC2 host through Session
Manager and run the host-managed check:

```sh
sudo /usr/local/sbin/duducar-command readiness
sudo /usr/local/sbin/duducar-stack status
sudo systemctl is-active duducar.service
```

The web container must run its default Gunicorn command behind Caddy. Do not
start `process_media --loop` in production. A real test upload must dispatch
the bounded `duducar-signage-production-ec2-media-worker` Fargate task, pass
quarantine/scanning/normalization, and let the task exit.

## Timers, backups, and public routes

The former Fargate schedules are replaced by five systemd timers on the EC2
host. Verify all are enabled and waiting:

```sh
sudo systemctl is-active \
  duducar-health.timer \
  duducar-playlists.timer \
  duducar-media-reconcile.timer \
  duducar-retention.timer \
  duducar-backup.timer
sudo systemctl list-timers 'duducar-*'
```

Run and verify the production logical-backup workflow through its managed
wrapper:

```sh
sudo /usr/local/sbin/duducar-command backup
```

Confirm the new archive and SHA-256 sidecar exist in the private versioned
backup bucket, and confirm a current DLM-managed data-volume snapshot exists.
An enabled DLM policy without a completed snapshot is not recovery coverage.

Verify Caddy, DNS, TLS, and application routing from outside the host:

```sh
curl --fail --show-error https://marketing.duducaradmin.com/health/live/
curl --fail --show-error https://api.marketing.duducaradmin.com/health/ready/
```

Also verify unsigned or expired CloudFront media requests are denied and a
fresh signed validated-media URL succeeds.

Development and production must use separate databases, buckets, secrets,
credentials, enrollment codes, backup roots, and device identities. Set
`DEPLOYMENT_ENV` explicitly in each environment and never reuse production
credentials locally.

See `docs/production-deployment-runbook.md` for the complete change and canary
gate, and `docs/backup-restore.md` for current recovery procedures.
