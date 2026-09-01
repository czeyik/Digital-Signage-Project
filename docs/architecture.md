# Current Pilot Architecture

## Decision

Use a modular Django 5.2 LTS monolith for the dashboard and versioned device API,
PostgreSQL 16 for durable data, private S3-compatible object storage for media,
and a native Kotlin Android application for playback.

The dashboard uses server-rendered HTML and small progressive enhancements. This
keeps authentication, authorization, CSRF protection, audit logging, and
business rules in one deployable service without requiring a JavaScript build
system.

## Runtime Shape

- `backend/`: Django dashboard, REST API, scheduled maintenance commands, media
  metadata, reporting, and audit history.
- `android-player/`: Android 12+ kiosk player with SQLite-backed offline
  state, scheduled heartbeat/synchronization, and platform image/video playback.
- PostgreSQL: users, assignments, immutable playlists, telemetry, alerts, and
  proof-of-play.
- Private object storage: quarantined uploads and validated media objects.
- OpenMapTiles vector basemap: a verified Malaysia MBTiles extract mounted
  read-only on the web host and served through authenticated same-origin
  dashboard routes; no third-party map API key is required.
- Media worker: ClamAV and FFmpeg/FFprobe processing. It may run in the web
  container for local development but must run separately in production.

## Trust Boundaries

- Dashboard users authenticate with secure server sessions and CSRF protection.
- Devices enroll with a 15-minute code and receive separate playback-refresh
  and management credentials. Playback refresh credentials are exchanged for
  one-hour bearer access tokens.
- Persistent credentials and access tokens are stored only as hashes by the
  server. Playback disablement revokes playback credentials but retains the
  management credential, whose only authority is polling and acknowledging a
  short-lived remote Admin mode command. Re-enrollment rotates both.
- Device endpoints never trust a device-supplied assignment, duration, media
  identity, or playlist identity without matching it to server records.
- Media remains quarantined until scanning and normalization succeeds; the
  stored delivery object is re-read and verified before it can be published.
- Object storage is private; clients receive expiring URLs for authorized
  objects.

## Deployment

Development and production use separate databases, buckets, secrets, hostnames,
and enrollment namespaces. Local development uses Docker Compose with
PostgreSQL and filesystem media storage; local files are never production data.

Production is live in `ap-southeast-5` with this USD 30/month target topology:

- one Amazon Linux ARM64 `t4g.small` EC2 instance;
- Caddy, Django/Gunicorn, and PostgreSQL 16 containers managed by systemd;
- an encrypted 8 GB GP3 root volume and encrypted 32 GB GP3 data volume;
- one Elastic IP with ports 80/443 public, PostgreSQL restricted to the
  dedicated worker security group, and operator access through Session Manager;
- private S3 media and backups encrypted with the project KMS key;
- CloudFront origin access control and signed URLs for validated media;
- one bounded ARM Fargate task per media-processing dispatch. After the
  reviewed Terraform and web-image release that introduces dispatch caps, it
  is limited to two active tasks and six `RunTask` calls per hour (including
  failed or ambiguous calls); an ambiguous result reuses its idempotency token
  for at most 15 minutes before consuming a new attempt, with no continuously
  running worker; and
- daily logical PostgreSQL backups plus a DLM policy scheduled to retain 30
  encrypted data-volume snapshots.

The legacy ECS web service and schedules, Application Load Balancer, and live
RDS database were removed on 2026-07-28. The retained ECS cluster exists only
to run isolated media tasks. Historical migration controls are not a rollback
mechanism.

## Scale Path

The pilot writes heartbeats and proof-of-play in append-only batches. Database
indexes use device and event time as leading keys. At 1,000 devices, media stays
on object storage/CDN, API processes remain stateless, and media processing can
scale independently without splitting the transactional application. Moving
PostgreSQL back to a managed database or splitting web capacity across hosts
requires a separately reviewed migration and budget; do not provision that
three-year shape for the 10-device pilot.

## Known Limits

- Pilot proof-of-play is commercially useful, not independently audited or
  tamper-proof.
- Factory reset protection and true screen-state reporting depend on qualified
  hardware.
- The production player can receive DUDU-owned APK updates from the authenticated
  sync response. It downloads only a higher version signed by the installed
  certificate, verifies the exact SHA-256/size, installs through the device-owner
  PackageInstaller path, and keeps the current app playing until the update is
  staged. The first updater-enabled APK still requires the existing post-Setup
  Wizard ADB install; later OTA delivery requires separate authorization and all
  OTA release configuration remains disabled for this framework.
- Media scanning requires ClamAV in the deployed processing environment.
- The current EC2 host contains both Django and PostgreSQL and is therefore a
  single-host failure domain. Daily logical backups, DLM snapshots, and tested
  recovery procedures support the accepted 24-hour RPO/RTO.
- The pilot serves OpenMapTiles directly from MBTiles through Django. Tile
  generation/update and high-concurrency delivery are intentionally outside
  the pilot; move the same TileJSON/style contract to a dedicated tile server
  or CDN before materially increasing dashboard traffic.
