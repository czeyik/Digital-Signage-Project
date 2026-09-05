# DUDU Car Digital Signage — Project Essentials

## Authority

Follow this guide unless the user explicitly changes a requirement. Do not
silently weaken security, product behaviour, pilot scope, or architecture;
explain the conflict and obtain a decision. Read the relevant canonical source
before changing it:

- `docs/architecture.md` — topology and trust boundaries
- `docs/device-api.md` — device and enrollment contract
- `docs/backup-restore.md` — backup and isolated recovery
- `docs/production-deployment-runbook.md` — release and canary gates
- `docs/hardware-qualification.md` — exact-device acceptance
- `docs/android-*.md`, `infrastructure/README.md` — build, signing, operators

## Pilot and production boundaries

- Deliver a 10-vehicle Malaysia pilot, designed to scale to 1,000 devices;
  use English and `Asia/Kuala_Lumpur`. Keep AWS at the USD 30 monthly target.
- Keep development and production databases, buckets, secrets, credentials,
  enrollment codes, and device identities completely separate.
- Use `marketing.duducaradmin.com` and `api.marketing.duducaradmin.com`.
- Production is one `ap-southeast-5` ARM64 `t4g.small`: systemd runs
  Caddy/Django/PostgreSQL; encrypted 8 GiB root/32 GiB data volumes, EIP and
  SSM only (no SSH). Private S3/CloudFront signed delivery and bounded
  on-demand Fargate processing support a 24-hour RPO/RTO.
- Legacy ECS web, ALB, and live RDS are not rollback paths. A topology change
  needs a reviewed, costed migration. Do not mutate production, DNS/ingress,
  backups/recovery, or enroll/canary a device without explicit authority.

## Roles, scope, and privacy

- Owner controls users, recovery, global settings, driver PII, and device
  lifecycle (provisioning, PINs, enrollment/credential revocation, and
  disable/reactivate). Marketing controls non-PII media and playlist operations
  and may acknowledge operational alerts; drivers have no dashboard. No public
  signup; dashboard accounts require `@duducar.co`; only owners view/enter
  driver names.
- Pilot scope: dashboard, Android 12+ kiosk, media/playlists, device lifecycle,
  offline sync, GPS/location with self-hosted OpenMapTiles, proof-of-play,
  alerts/audit, and 30-day backups.
- Deferred unless explicitly approved: news/weather, passenger interaction,
  audio/camera/mic, PDF/GIF/APNG, advertiser/approval workflows, remote
  updates, advanced fleet/shifts, MFA, independently audited evidence.
- Collect no passenger data/unneeded permissions. Store only driver name,
  system-generated internal ID, registration, and assignment SIM telephone
  number; reports use IDs only. Never log PII, secrets, tokens, or raw media
  URLs. Only owners may view assignment telephone numbers. Anonymize name,
  registration, and SIM telephone number one year after final unassignment;
  retain/anonymize event records after one year as appropriate.

## Non-negotiable product rules

- Media: JPEG/PNG/MP4 only; images <=10 MB for 15 seconds, video <=50 MB,
  <=15 seconds/1080p. Quarantine until scan/type/decode/silent-H.264-normalize
  and test pass; fit without crop on black; private time-limited delivery;
  preview/reuse allowed; do not delete current/future playlist media (retain
  metadata/audit after deletion).
- Playlist: one fleet-wide list; non-empty, configurable 100 entries/30 min;
  immutable versions; arbitrary start/end times with deterministic overlap
  precedence. Scheduled activation
  occurs at the server-defined boundary after complete valid download; any
  changed active manifest replaces immediately after validation. Keep the prior
  valid list on any failure or missing change.
- Player: locked landscape, screenshots blocked where supported, awake,
  battery-backed. Never depend on/infer vehicle power, occupancy, audience, or
  misconduct. Only remotely entered Admin mode exposes Exit DUDU and Prepare
  for shutdown; normal playback, fallback, maintenance, and enrollment expose
  no local admin gesture, PIN flow, exit, or shutdown action. Resume from the
  neutral prepared-shutdown state is explicit, never lifecycle-driven.
- Restart interrupted media at its start; skip bad media without blank screen;
  fallback is not proof. Disable means maintenance until explicit reactivation;
  updates are staff sideloads. Android 13 queues idempotent abnormal-exit
  diagnostics after a successful time sync.
- Devices: independent revocable identity, short-lived encrypted credentials,
  no shared key. Owner-confirmed one-time 15-minute, root/integrity-checked
  enrollment; separate management authority survives playback disablement but
  explicit credential revocation removes both channels; preserve assignments.
- Offline: 10 GiB cache, startup/hourly sync plus exact scheduled-boundary sync,
  server-corrected time, retain old valid content until activation, delete only
  after activation. Queue cap 500 MB; evict acknowledged records first and
  record forced loss. Warn after one missed day; critical retrieve alert after
  three days.
- Proof: idempotent per-loop/per-entry results with assignment, immutable media
  and playlist, corrected time, state, duration/reason/offline flag. Only a
  full image/video counts; append-only evidence, 7-day finalization; reports
  distinguish states and say they are not audited/tamper-proof.
- Health: heartbeat 30 min/offline 60 min; alerts stay open until acknowledged.
  Battery <=20% warning, <=10% same alert critical; 24/48h unavailable
  warning/critical; three exits/24h, <2 GiB, three ad failures, version drift,
  reliable thermal.

## Security, delivery, and verification

- Internet-facing dashboard/API: server-side authorization/validation, strong
  passwords, rate-limit/lockout/generic errors, 30-minute sessions, single-use
  15-minute reset invalidating sessions, suspicious-login/device-auth alerts,
  TLS/headers/CSRF, least privilege and encryption. Logout revokes only its
  dashboard session, never device tokens.
- Audit auth, sensitive PII access, content/publication/export, device/user
  actions and PIN resets immutably. Preserve audit, playback, playlist and
  assignment history. Never commit credentials, keys, recovery codes, PINs, or
  secret environment files; pin/scan production deps and images.
- Use understandable maintained components; validate trust boundaries on the
  server; make retry APIs idempotent; avoid unrelated refactors. Emulator/phone
  tests do not substitute for exact model, firmware, security-patch, and
  compatible-display identity; physical test flags and evidence are optional
  observations.
- For backend/shared changes run relevant `ruff`, Django check/migration dry
  run, pytest, and readiness checks; use documented Android Docker/JDK17-SDK36
  build. Test the changed surface plus auth/privacy, offline/evidence, recovery
  and hardware boundaries. Report changed, tested, untested, and remaining risk.
