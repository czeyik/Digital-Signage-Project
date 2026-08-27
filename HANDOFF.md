# DUDU Car — Continuation Handoff

**Updated:** 2026-08-27, Asia/Kuala_Lumpur

## Goal

Safely reach the first **one-device production canary** for the 10-vehicle
Malaysia pilot on the existing low-cost AWS design. Keep the current Android
12+/API 31 requirement and production topology.

## Read first

1. `AGENTS.md` — development style and mode.
2. `OVERVIEW.md` — product, security, privacy, and operating constraints.
2. `docs/architecture.md` — approved EC2/SSM/private-media topology.
3. `docs/production-deployment-runbook.md` — ordered production gates.
4. `docs/hardware-qualification.md` and `docs/android-release-signing.md`.

## Current source and workspace

- The `origin/main` baseline used for this work is
  `196655d91aa3466c9697a6cc4a8f887225e32edf`.
- The DUDU-owned updater originated in commit
  `1cbcd587aff263ca5aa73205f94b6af8b703697c`; the combined GPS/OpenMapTiles
  source is clean commit `ea7394c` on `feat/dudu-owned-updater` at
  `/tmp/duducar-openmaptiles`.
- Do **not** build or deploy from this primary workspace: it is
  `fix/recovery-xfs-journal-replay` at `ab073e8` and has uncommitted
  documentation changes.
- The earlier reviewed clean release source at
  `/tmp/duducar-owner-smoke-main-44dac05` is historical; do not use it for
  this updater release.
- The concise AGENTS revision is commit `c60d259` on
  `docs/condense-agent-playbook`; it is not merged into `main`.
- Recovery CSV acceptance was merged through PR #46: a header-only unfiltered
  playback CSV is valid only when the restored source has zero playback events;
  never seed or fabricate evidence.

## Decisions and completed evidence

- The Android 10 proposal is **rejected**. Remain on Android 12+/API 31.
- The connected P60 Pro is disqualified: ADB reports Android 10/API 29 and
  platform patch `2021-03-05`, despite its UI/listing claims. No DUDU APK,
  device owner, credentials, or production enrollment was placed on it.
- Do not flash unofficial firmware. No authoritative OEM Android 12+ image,
  checksum, or upgrade path was found for its exact board/build.
- Recovery drill `1c26e94f6ed2ba05e2b7fd4d2c4de9c2` passed and was destroyed.
  The selected source had zero playback events, so its unfiltered CSV was
  header-only; the owner accepted this as export-path evidence only. Never
  fabricate playback rows or reuse that operation/state/resources.
- The retained signed canary APK is `com.duducar.signage` 1.0.1 / code 2,
  signed with the new RSA-4096 company key. APK, R8 mapping, checksums, and
  non-secret evidence are in the encrypted company vault; two independent
  encrypted key backups are confirmed. Never commit, print, or copy the key.
- ARM64 backend image was built/scanned and pushed with no HIGH/CRITICAL ECR
  findings: `173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:b9a3dc42c985fd28485bc0cc679e8f6c19706e70c14927485728e0caab60d2be`.
- DUDU-owned OTA is implemented in commit `1cbcd587aff263ca5aa73205f94b6af8b703697c`:
  the player accepts only a higher same-certificate APK, verifies HTTPS,
  package/version, exact SHA-256/size, device-owner state, storage, and safe
  power conditions, then uses the device-owner PackageInstaller path. The
  backend advertises deterministic rollout metadata only from the reviewed
  private `updates/*.apk` prefix.
- The updater-enabled production APK is `com.duducar.signage` 1.0.2 / code 3,
  SHA-256
  `e8d7be7962814e73835513dca9cacadf8164028954b266bb19ec70e3a1ed0796`,
  1,214,469 bytes, certificate SHA-256
  `4f1d915a83096448a78528ae1fe4c6e484ed95bdf6c276bff877f59da1b000e5`.
  It matches the retained code-2 signing certificate. The APK is retained
  outside Git and uploaded as `updates/dudu-signage-1.0.2.apk` in the private
  production media bucket.
- The updater-release ARM64 backend image was built, scanned with zero
  HIGH/CRITICAL findings, and pushed to ECR at
  `173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:8ffc69e94fe693abd29d9ca64270e5c892cf03386ea5946048c6694aa1fff8bf`.
- Validation completed for the updater source: backend tests (`254 passed,
  2 skipped`), Ruff, Android production/development compile and lint, APK
  signature verification, Terraform format/validate/topology test, runtime
  guardrails, release-config behavior test, and bootstrap-size check.

## Production preparation status (last verified 2026-08-26)

> **Updater release update (2026-08-26):** commit `1cbcd587aff263ca5aa73205f94b6af8b703697c`
> contains the complete DUDU-owned OTA path, release-config fields, CloudFront
> `updates/*` authorization, and the credential-broker recovery fix. The new
> backend image and code-3 APK are staged in production artifact stores, but
> no Terraform plan/apply, release-config install, migration, or host
> activation has been run from this commit. The previously recorded `08cc...`
> install operation and all prior activation/recovery IDs are stale and must
> not be resumed. Start a fresh reviewed plan, document versions/hashes, and
> operation ID. Before activation, add exact digest-pinned `postgres_image` and
> `caddy_image` values, set `required_app_version = "1.0.2"`, keep all six
> `app_update_*` fields disabled for the first manual updater installation, and
> verify `WORKER_DB_PASSWORD` plus canonical
> `PLAY_INTEGRITY_APP_CERTIFICATE_SHA256` in the application secret. The sole
> supported activation path is `production_release_activation_document`; it
> owns deploy, migration `0012`, runtime grants, readiness, digest/version/unit
> assertions, and the post-release verified backup. Do not run those commands
> manually.

- Terraform has not applied the updater commit. Existing AWS release-support
  assets remain from the prior reviewed scope; the new runtime-bundle,
  release-config, CloudFront policy, broker, and app-update fields require a
  fresh reviewed plan/apply before activation.
- The first updater-enabled code-3 APK must be installed manually after Setup
  Wizard and device-owner assignment. It is not an OTA target for code-2
  tablets, which do not contain the updater. Build a later strictly higher
  code (for example code 4) before enabling `app_update_*` rollout.
- Release-config validation operation `08ccbebf3c7672087b96875e76884479`
  belongs to the superseded document and must never be installed or resumed;
  follow the updater-release note above with a fresh operation.
- Migrations `0009`–`0012`, notification end-to-end test, production rehearsal,
  and vehicle enrollment remain pending. Re-run read-only AWS/owner preflight
  before any production action; do not treat this dated status as live proof.

## GPS tracking implementation status (2026-08-27)

The foreground-only GPS feature is implemented in the current GPS working
tree, but has not been deployed or enabled in production. The Android player
uses native `LocationManager` GPS/network providers while the enrolled
activity is visible, records a qualifying non-mock fix every minute, persists
unsent points in SQLite (newest 50,000 cap), and uploads gzip batches of up to
500 with current-point priority and idempotent acknowledgements. Device-owner
policy grants precise location and enables location services; playback,
fallback, maintenance, shutdown, and connectivity paths remain independent of
location collection.

The backend migration `0014_location_tracking` adds the indexed PostgreSQL
point model, historical assignment binding, cached state, 30-day retention,
authenticated batch ingestion, permanent rejection handling, and minute-level
health evaluation. Owner/marketing dashboard routes expose only device label,
vehicle registration, driver internal ID, coordinates, recorded/received time,
provider, accuracy, current state, and a single-device maximum-24-hour raw
polyline. No driver names, public endpoint, reverse geocoding, geofencing, or
location writes are included. Batch processing logs counts only (never
coordinates or point payloads); queue overflow, rejected points, and stale or
disabled devices create operational alerts.

The map uses the pinned self-hosted MapLibre GL JS/CSS v5.6.0 bundle and the
same-origin OpenMapTiles service:

- JS SHA-256: `0bec2961695addc0ed69b4b6e35cf0d545d23677e290d57f2a4f5d10815c12fc`
- CSS SHA-256: `792ac997dcf6ae6f643eb4e2dee4630c85e7056526bd8fb85ffe83c67d6c41b4`

The dashboard no longer calls a third-party map API or exposes a map key. It
serves an OpenMapTiles-compatible style and TileJSON document from authenticated
same-origin routes and reads vector tiles from a verified, read-only MBTiles
file mounted at `/openmaptiles/malaysia.mbtiles`. Terraform creates the host
directory and passes the path into the web container; the map-data file still
needs to be installed through the reviewed operator transfer procedure. Keep
OpenStreetMap/OpenMapTiles attribution and refresh the extract on its own
reviewed schedule. Tile availability, ingestion/rejection counts, overflow
alerts, evaluator duration, and database growth still require the normal
production monitoring review.

Before rollout, run a fresh reviewed migration/plan and the supported release
activation path, then build a signed GPS APK at a version code strictly higher
than the current code 3 (code 4 is the next eligible OTA target). Do not enable
the rollout until the exact Android 12+ tablet/firmware passes GNSS cadence and
accuracy, offline replay/reconnect, permission, battery, thermal, and
mock-location qualification; provide the documented driver notice; and run the
one-device 72-hour canary. No Terraform apply, migration, OpenMapTiles data
installation, APK publication, or production activation has been run for this
GPS change.

## Blocking gates

1. Obtain an exact Android 12+ tablet or OEM-signed Android 12+ firmware with
   documented support and current verified patch level.
2. Complete full exact-model/firmware hardware qualification, including
   device-owner/lock-task, battery, LTE, media, shutdown/recovery, thermal,
   kiosk escape, and factory-reset revocation evidence.
3. Require a real `MEETS_DEVICE_INTEGRITY` result before production enrollment.
4. Complete the remaining runbook rehearsal, controlled SNS/EventBridge
   notification delivery test, and owner/migration preflight.
5. After the fresh reviewed plan, generate a new operation ID and use the
   supported activation document. A stopped/failed-existing host requires a
   fresh, operation-correlated ARM then RECOVER authorization; never reuse a
   prior operation or SSM command.

## Do not do

- Do not lower `minSdk`, modify the Android 10 policy, or use the P60 Pro for
  production/canary without a verified OEM upgrade and fresh qualification.
- Do not run raw Terraform, SSH, add ingress, hand-edit production env files,
  use a stale plan, install the SSM config, migrate, or enroll a device without
  the applicable explicit confirmation and all preceding gates.
- Do not use production credentials, DNS, system trust, or browser profiles for
  recovery/testing; do not expose secrets, signing material, PINs, tokens, or
  personal data in Git, logs, chat, or handoff notes.

## Very next step

Obtain an Android 12+ candidate and run a read-only ADB preflight before
installing anything: record model, firmware, Android API
(`ro.build.version.sdk >= 31`), platform/vendor patch, verified-boot state, and
Google Play availability. If it passes, begin the documented exact-device
qualification in an isolated workflow. Only after the `HardwareQualification`
record has an exact model/firmware/security-patch match, an attested
9.00–12.00-inch display, and a nonempty evidence reference should the code-3
APK be installed manually after Setup Wizard and device-owner assignment.
Keep OTA disabled until a later higher-code APK is reviewed and the canary is
healthy. Full physical approval remains tracked separately.
