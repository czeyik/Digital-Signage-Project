# Production Readiness Checks

Use these checks for the EC2 topology in [`architecture.md`](architecture.md).
The [production runbook](production-deployment-runbook.md) remains the release
and canary authority.

## Application

Before building the release image, run in a production-like environment:

```sh
python manage.py check_deployment_readiness --environment production
```

This must confirm production hostnames, PostgreSQL, private object storage,
secure cookies/HTTPS/proxy handling, SMTP, and media tools, with debug and
console-only email disabled. The command refuses a production check unless the
runtime itself has `DEPLOYMENT_ENV=production`; set
`PLAY_INTEGRITY_APP_CERTIFICATE_SHA256` to the approved comma-separated APK
certificate SHA-256 allowlist before running it.

After deploying the pinned image through SSM, run:

```sh
sudo /usr/local/sbin/duducar-command readiness
sudo /usr/local/sbin/duducar-stack status
sudo systemctl is-active duducar.service
```

Django/Gunicorn must run behind Caddy. Production must not run
`process_media --loop`; a test upload must launch the bounded isolated Fargate
worker, complete quarantine/scanning/normalization, and let the task exit.

## Timers and recovery layers

Verify all five host timers:

```sh
sudo systemctl is-active \
  duducar-health.timer \
  duducar-playlists.timer \
  duducar-media-reconcile.timer \
  duducar-retention.timer \
  duducar-backup.timer
sudo systemctl list-timers 'duducar-*'
```

Run the managed backup once:

```sh
sudo /usr/local/sbin/duducar-command backup
```

Require a versioned private archive, matching SHA-256 sidecar, and a current
completed remote-success receipt
`duducar-signage-postgres-last-remote-success.json` naming both uploaded object
version IDs and checksums, plus a current completed DLM data-volume snapshot.
An enabled policy is not restore evidence;
follow [`backup-restore.md`](backup-restore.md) for the recovery gate.

## Public routes and media

From outside the host, verify:

```sh
curl --fail --show-error https://marketing.duducaradmin.com/health/live/
curl --fail --show-error https://api.marketing.duducaradmin.com/health/ready/
```

Also require HTTP-to-HTTPS redirection, valid TLS, a successful fresh signed
validated-media request, and denial of unsigned, expired, modified,
quarantined, or arbitrary object requests.

Stop if development and production share a database, bucket, secret,
credential, enrollment code/root, backup root, or device identity. Set
`DEPLOYMENT_ENV` explicitly and never use production credentials locally.
