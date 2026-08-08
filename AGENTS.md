# DUDU Car Digital Signage Agent Playbook

## Purpose

This file is the authoritative repository-level guide for coding agents working on
DUDU Car's in-car digital signage platform. Follow it unless the user explicitly
changes a requirement.

Do not silently weaken security controls, alter product behavior, or expand the
pilot scope. When a requested change conflicts with this file, explain the
conflict and confirm the new decision before implementing it.

This is an agent playbook, not the complete architecture specification. The
selected application and production topology is recorded in
`docs/architecture.md`; preserve that decision unless a reviewed architecture
change explicitly replaces it.

## Product Goal And Constraints

- Deliver a controlled pilot within one month for 10 vehicles in Malaysia.
- Design application boundaries and data models to scale toward 1,000 devices
  within three years, while provisioning only pilot-scale infrastructure.
- Keep the AWS project at or below the **USD 30 monthly target**, excluding the
  RM 40 monthly mobile-data allowance for each tablet. Treat unusual media
  egress, snapshot growth, and on-demand worker duration as budget risks rather
  than assuming the target is guaranteed.
- Use `Asia/Kuala_Lumpur` for all business scheduling and user-facing times.
- Use English for the pilot interfaces.
- Host separate development and production environments with separate
  databases, object storage, secrets, credentials, and device enrollment.
- Prefer an AWS region in Malaysia when the required services and pricing are
  suitable; otherwise evaluate Singapore. Do not infer a region from the
  account's billing currency.
- Use `marketing.duducaradmin.com` for the dashboard and
  `api.marketing.duducaradmin.com` for the API.
- Optimize for secure operation and one-person maintainability. Do not select a
  technology solely because the project owner knows Python.

## Current Production Architecture

Production in `ap-southeast-5` is intentionally pilot-sized:

- One Amazon Linux ARM64 `t4g.small` EC2 instance runs Caddy, Django, and
  PostgreSQL containers under systemd.
- The instance uses an encrypted 8 GB GP3 root volume, an encrypted 32 GB GP3
  data volume, one Elastic IP, and Session Manager; SSH is not exposed.
- Media remains in private S3 and is delivered through CloudFront origin access
  control and signed URLs.
- Each upload may dispatch one bounded, isolated ARM Fargate media-processing
  task. There is no continuous media worker.
- Daily logical PostgreSQL backups go to private versioned S3, and DLM is
  configured for daily encrypted data-volume snapshots with 30 retained
  recovery points.
- The legacy ECS web service and schedules, Application Load Balancer, and live
  RDS instance have been removed. They are not a current rollback path. The ECS
  cluster remains only for isolated on-demand media tasks.

This single-host topology accepts a pilot-level failure domain to meet the USD
30 target and 24-hour RPO/RTO. A return to managed PostgreSQL or independently
scaled web services requires a new costed architecture decision and data
migration plan.

## Pilot Users And Roles

The pilot has three roles:

- **Account owner:** Manages users, recovery, driver personal data, global
  settings, and all marketing functions. The head of marketing is the initial
  owner.
- **Marketing user:** Manages media, playlists, devices, alerts, reports, and
  CSV exports.
- **Driver:** Has no dashboard access. Any physical user may use the visible,
  deliberately non-PIN-protected **Prepare for shutdown** control, then use the
  approved tablet's documented physical power-off method. That control is not
  dashboard or administrator access.

Public registration is forbidden. Create the initial account through a one-time
deployment command. Dashboard accounts must use `@duducar.co` email addresses.
The owner may create or deactivate the two additional dashboard users expected
within three years.

## Pilot Scope

The pilot must provide:

- A desktop-browser marketing dashboard.
- An Android 12 or newer kiosk player.
- Media upload, quarantine, validation, preview, reuse, archival, and deletion.
- Draft, future, published, and urgent-replacement playlists.
- Offline playback and reliable content synchronization.
- Device enrollment, assignment, reassignment, disablement, and health
  monitoring.
- Proof-of-play collection, dashboard charts, and CSV reports.
- Dashboard alerts and immutable audit history.
- Daily automated backups retained for 30 days.

The acceptable recovery point and recovery time are both 24 hours.

## Deferred Features

Do not add these features to the pilot unless the user explicitly changes scope:

- News and weather
- GPS, precise location, or geographic targeting
- PDF reports
- GIF or APNG upload and conversion
- Advertiser accounts
- Separate content approval workflows
- Remote application updates
- Advanced fleet management or driver shift scheduling
- Passenger interaction, touch controls, audio, camera, or microphone access
- Independently audited or tamper-resistant billing evidence
- Multi-factor authentication

MFA is deliberately deferred, despite the increased account-takeover risk. Keep
free authenticator-app MFA and recovery codes as a priority immediately after
the pilot.

## Media Rules

- Accept JPEG, PNG, and MP4 only.
- Images are limited to 10 MB and display for exactly 10 seconds.
- Videos are limited to 50 MB, 15 seconds, and 1920x1080.
- Normalize every accepted video to a tested, silent H.264 MP4 at 1080p or
  lower. Remove all audio tracks.
- Fit portrait, square, and other aspect ratios without cropping, using a black
  background where necessary.
- Keep uploads quarantined until malware scanning, type and size checks,
  decoding, conversion, audio removal, and test playback all succeed.
- Reject any file that cannot complete the processing pipeline.
- Allow preview of images and videos in the desktop dashboard before
  publication.
- Allow one processed media asset to be reused in multiple playlists.
- Do not expose object-storage buckets publicly. Deliver media through
  authorized, time-limited access.
- Block deletion while media is referenced by any current or future playlist.
  Once unused, remove the binary when deletion is confirmed, preserve the
  historical metadata, and create an audit event.

## Playlist Rules

- All pilot devices receive the same playlist.
- A playlist may contain advertisements from multiple businesses.
- Each entry plays once per loop. Deliberately duplicating an entry is allowed
  and transparently gives that advertisement additional rotation.
- The tentative limits are 100 entries and 30 minutes per loop. The account
  owner may change these system-wide limits later.
- Calculate loop duration from actual normalized video durations plus 10
  seconds for each image.
- Block publication of an empty playlist or one exceeding either configured
  limit.
- Marketing arranges entries using drag and drop.
- Play entries with immediate transitions and no blank interval between loops.
- Marketing may prepare and publish multiple future weekly playlists.
- A weekly playlist runs from Monday at 12:00 PM through the following Monday
  at 11:59:59 AM, Malaysia time.
- Marketing must publish playlists manually. A missing replacement leaves the
  current valid playlist playing indefinitely and creates a warning.
- Published playlist versions are immutable. Every correction creates a new
  version so historical reports retain the exact media versions and ordering.
- A normal scheduled replacement activates only after it is fully downloaded
  and validated on-device, and after the current loop finishes.
- Marketing may publish an urgent replacement. It activates on each online
  device immediately after the complete replacement downloads and validates;
  it does not alter already scheduled future playlists.
- Reject the complete replacement if any item is missing or fails on-device
  validation. Continue the previous valid playlist and report deployment
  failure.

## Android Player Behavior

- Operate as a locked, landscape kiosk. The visible **Prepare for shutdown**
  control is the sole intentional non-administrator interaction.
- Hide Android navigation, block screenshots where supported, and prevent
  device sleep during playback.
- Support only approved battery-backed Android tablets. The battery-free
  display path is not part of this pilot.
- Play advertising while the tablet itself has power, including battery power
  while parked or disconnected from vehicle power. Do not gate playback,
  screen-on behavior, or synchronization on external vehicle-power detection.
- A normal visible **Prepare for shutdown** button must be available without a
  PIN. After confirmation, interrupt the current item, queue its result, show
  a neutral shutdown-ready screen, and remain stopped until a physical user
  confirms the visible non-PIN **Resume DUDU** action. Restoring the launcher,
  activity, or lifecycle alone must not resume advertising. There is no
  five-minute automatic resume.
- The user then follows the exact approved tablet's documented physical
  power-off method. After battery depletion or a reboot, recovery is best
  effort: staff unlock and launch DUDU to reach the shutdown-ready screen, then
  confirm **Resume DUDU** before playback can resume.
- Do not label an interruption as a confirmed vehicle-power loss or driver
  misconduct. On Android 13, after a successful manifest sync anchors server
  time (on that sync or a later one), inspect historical process-exit reasons
  and queue idempotent non-sensitive diagnostic operational events for abnormal
  application exits.
- After restart, begin the interrupted playlist item again from its start, then
  continue the loop.
- Record an interrupted result when the app restarts after crashing, an
  unplanned shutdown, or another mid-item interruption.
- Skip a failing current item immediately without showing a blank screen,
  record the failure, and continue the active playlist.
- Ship neutral DUDU fallback media inside the app. Do not count fallback media
  as proof of play.
- Remote disablement takes effect as soon as the device is online, stops all
  advertising, and shows a neutral maintenance screen. Reactivation must be an
  explicit dashboard action.
- Install pilot application updates manually by company staff through
  sideloading.

There is no ordinary in-app pause control, pause reason, or scheduled shift.
The deliberately available shutdown flow is the exception described above.
Offline time and an interruption do not prove driver misconduct, vehicle
operation, vehicle occupancy, external power, or audience exposure.

## Device Enrollment And Assignment

- Give every device an independent identity that can be revoked without
  affecting other devices.
- Never embed a shared long-lived API key in the application.
- Use short-lived device credentials and authenticated, encrypted
  communication.
- Require an authorized dashboard user to confirm device, car, and driver
  assignment details before creating a one-time enrollment code. Only the
  account owner may enter or view driver names; marketing workflows may use
  internal driver IDs and vehicle registrations after the assignment exists.
- Enrollment codes expire after 15 minutes, work exactly once, and become
  invalid immediately after successful use.
- Reject enrollment on rooted devices or when required device-integrity checks
  fail.
- Generate a unique kiosk administrator PIN for each device. Show it once to
  the account owner, store only a secure verifier, and permit reset rather than
  later disclosure.
- Permit online reassignment to a different car or driver without reinstalling
  the application. Preserve the complete historical assignment timeline.
- Factory reset protection is not guaranteed without paid device management.
  Accept this pilot limitation; revocation protects server access only while
  device credentials remain present.

## Synchronization And Offline Operation

- Reserve up to 10 GB of local storage for advertising media.
- Check for synchronization at startup and hourly until that day's
  synchronization succeeds.
- Target the daily synchronization at 12:00 AM Malaysia time and download only
  changed content.
- Synchronize with server time whenever connected and retain the corrected
  offset for offline event timestamps and playlist activation.
- Never depend solely on a user-editable device clock for business scheduling.
- Continue the last valid cached playlist during network loss, interrupted
  downloads, or failed replacement validation.
- Continue cached content even when it belongs to an older weekly schedule,
  until a valid published replacement activates.
- Remove obsolete files only after replacement activation succeeds.
- Create a missing-sync warning after one day and a critical retrieve-device
  alert after three days.
- Retain offline event batches until the server acknowledges them.
- Cap the event queue at 500 MB. Remove oldest acknowledged records first.
  Preserve unacknowledged records unless device storage is critically low, and
  record any forced data loss.

## Proof Of Play

For each completed or interrupted loop, create one compressed batch containing
a result for every playlist entry:

- Event and batch identifiers suitable for idempotent ingestion
- Device identity
- Car and internal driver assignment at playback time
- Immutable playlist version and ordered entry identity
- Normalized media version
- Server-corrected original playback timestamp
- Completed, interrupted, or failed status
- Completed duration or interruption point where available
- Non-sensitive failure reason
- Whether the event was captured offline and uploaded later

Only fully completed advertisements count as contractual plays. A completed
image must remain continuously visible for its full 10 seconds. A completed
video must reach its natural end within an implementation-defined, tested
playback tolerance. A completed advertisement counts whether it played while
the tablet was battery-powered or parked; that evidence does not prove vehicle
operation, occupancy, external power, or audience exposure.

Detect duplicate batches and events so reconnects cannot double-count plays.
Once accepted by the server, playback evidence is immutable; corrections must
be appended rather than editing or deleting original evidence. Finalize
contractual reports after a seven-day grace period for offline uploads.

Reports must group by media/campaign, device, car, internal driver ID, and date.
CSV exports must use internal driver IDs and must not contain driver names.
Reports must distinguish completed, interrupted, failed, and offline-captured
results and expose non-sensitive failure categories.

Pilot reports are commercially useful evidence only. Clearly state that they
are neither independently audited nor tamper-proof.

## Device Health And Alerts

- Send a heartbeat every 30 minutes.
- Mark a device offline after 60 minutes without a heartbeat.
- Show offline status in the fleet view without inferring why the device is
  unavailable.
- Keep alerts open until a dashboard user acknowledges them.
- Show a prominent unresolved-alert summary and fleet-status counts on the
  dashboard home page.
- New player heartbeats report battery level, reliable screen state, available
  storage, app version, Android version, temperature when available, active
  playlist, last successful sync, and last playback. They do not report
  external-power or charging state. The server may retain legacy heartbeat
  fields during an APK rollout, but those values are not health-policy inputs.
- At battery `<=20%`, open a `low_battery` warning. At `<=10%`, escalate that
  same unresolved alert to critical. Neither threshold stops advertising.
- Use the server-received heartbeat time for availability: at 24 hours without
  one, open a `device_unavailable` warning; at 48 hours, escalate that same
  unresolved alert to critical.
- On Android 13, receive idempotent abnormal-application-exit diagnostics. At
  three distinct abnormal events received in a rolling 24 hours, open a
  `repeated_abnormal_app_exit` warning.
- Alert when free storage falls below 2 GB.
- Alert after any three advertisement failures on one device.
- Alert when a device differs from the required application version.
- Alert for overheating only on approved hardware that exposes trustworthy
  temperature data.

## Security Requirements

- Treat the dashboard and device APIs as internet-facing systems.
- Require strong passwords, login rate limiting, temporary lockouts, secure
  password hashing, and generic authentication errors.
- Restrict dashboard accounts to the company domain and disable public signup.
- End dashboard sessions after 30 minutes of inactivity.
- Make password-reset links single-use, expire them after 15 minutes, and
  invalidate existing sessions after a successful reset.
- Send suspicious-login and repeated-device-authentication alerts to the head
  of marketing through the dashboard.
- Enforce authorization server-side for every operation and data field.
- Record logins, failed authentication, content changes, publication, urgent
  replacement, exports, device enrollment and disablement, user changes,
  administrator-PIN reset, and access to driver personal data in immutable
  audit logs.
- Keep playback evidence and audit events outside ordinary delete workflows.
- Use TLS everywhere, secure headers, CSRF protection where applicable, strict
  input validation, least-privilege AWS identities, encryption at rest, and
  managed secret storage.
- Never commit production secrets, credentials, private keys, recovery codes,
  PINs, or environment files containing secrets.
- Scan dependencies and container images, pin production dependencies, and
  document remediation of known high-severity vulnerabilities.
- Use daily automated backups retained for 30 days and test restoration before
  relying on them.

## Agent Guidance: Logout handling

- **What to check:** The web dashboard uses Django's built-in `LogoutView` at [backend/config/urls.py](backend/config/urls.py#L16). The dashboard login is implemented by `SecureLoginView` in [backend/signage/views.py](backend/signage/views.py#L38). Agents should prefer linking to these files rather than copying logic.
- **Testing:** Verify that visiting `/logout/` invalidates the user's session cookie, prevents access to protected views (e.g., the dashboard), and redirects appropriately. Add an automated test that logs in, calls `/logout/`, then asserts a subsequent request to a `@login_required` view returns 302/redirect to login.
- **Caution:** Do not confuse dashboard session logout with device authentication. Device credentials and short-lived device tokens (used by the Android player) are separate and must not be revoked by dashboard logout flows unless the change is explicitly required and audited.
- **Audit & security:** Confirm login and failed-login events are recorded in `AuditEvent` (see `backend/signage/views.py`) and consider whether a logout audit event is needed for your change. Follow existing security rules in this file when modifying auth behavior.


## Agent Guidance: Local Tooling And Verification

Backend verification expects the repository virtual environment and these local
executables:

- Python dependencies from `backend/pyproject.toml`, including the `dev`
  extras.
- `ffmpeg` and `ffprobe` for video decoding and normalization checks.
- `clamscan` from ClamAV for production-equivalent malware scanning.

Use these backend checks before completing backend or shared behavior changes:

- `cd backend && ../.venv/bin/ruff check .`
- `cd backend && ../.venv/bin/python manage.py check`
- `cd backend && ../.venv/bin/python manage.py makemigrations --check --dry-run`
- `cd backend && env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 DJANGO_SETTINGS_MODULE=config.settings ../.venv/bin/pytest -p pytest_django.plugin`
- `cd backend && ../.venv/bin/python manage.py check_deployment_readiness --environment development`

Run production readiness checks against production-like environment variables
before launch:

- `cd backend && python manage.py check_deployment_readiness --environment production`

Android verification requires JDK 17 and Android SDK 36. If those are not
installed on the host, use the checked-in Docker build environment:

- `docker build -f android-player/Dockerfile.build -t duducar-android-build android-player`
- `docker run --rm -v "$PWD/android-player:/workspace" -w /workspace duducar-android-build ./gradlew :app:compileDebugKotlin --no-daemon`

If Docker is unavailable, install JDK 17 and Android SDK command-line tools with
platform 36 and build tools 35 or newer, then run:

- `cd android-player && ./gradlew :app:compileDebugKotlin --no-daemon`

Backup verification should be exercised before relying on backups:

- `cd backend && python manage.py create_pilot_backup --output-dir /secure/signage-backups`
- `cd backend && python manage.py verify_pilot_backup /secure/signage-backups/<archive>.tar.gz`

Hardware qualification is not complete until a `HardwareQualification` record is
created for the exact model and firmware, every required test is marked passed,
and the evidence reference points to photos, logs, and notes. Do not treat
emulator or phone testing as release acceptance for the 10-inch pilot hardware.


## Privacy And Retention

- Do not collect passenger information.
- Do not request GPS, camera, microphone, or unnecessary Android permissions.
- Store only driver name, internal driver ID, and vehicle registration for
  driver assignment. Do not store driving-licence numbers.
- Only the account owner may view driver names. Marketing reports use internal
  driver IDs and may show vehicle registrations. Enforce both restrictions
  server-side.
- Begin the driver-retention period when the driver is unassigned from their
  final tablet.
- After one year, anonymize driver name and vehicle registration while
  retaining non-identifying internal IDs required by historical proof-of-play
  records.
- Retain playback, operational, and audit records for one year from each
  event's timestamp, then delete or anonymize them according to their legal and
  reporting purpose.
- Do not put personal data, credentials, tokens, or raw media URLs in logs.

## Hardware Qualification

Do not claim a device supports kiosk operation, screen-state proof, automatic
boot, shutdown behavior, or temperature monitoring until it passes a recorded
qualification test.

Approved hardware must:

- Run Android 12 or newer and support tested device-owner/lock-task
  provisioning without a paid MDM service.
- Be battery-backed and safely sustain the documented battery-powered playback
  and charging/runtime path.
- Expose a reliable physical screen-on state.
- Pass the documented non-PIN planned-shutdown, physical power-off, and
  staff-unlock/launch-to-shutdown-ready/**Resume DUDU** recovery path.
  Automatic restart after vehicle power is supplied is not a release
  requirement.
- Report a reliable battery percentage for health thresholds and, on Android
  13 where supported, recover and report the documented abnormal-exit
  diagnostics.
- Meet suitable operating-temperature and direct-sunlight requirements.
- Include appropriate battery safeguards.
- Support company rules preventing devices from being left inside parked cars.
- Operate safely from the vehicle power supply with an approved mount and power
  adapter.

The pilot may use an Android phone and emulator for early development, but
release acceptance requires testing on the selected 10-inch landscape hardware.

## Engineering Expectations

- Preserve the selected architecture while implementing features. Any proposed
  topology change must first update the architecture plan based on
  functionality, security, the USD 30 AWS target, Android kiosk reliability,
  scalability, deployment simplicity, and one-person maintainability.
- Prefer established, maintained libraries and managed services where they fit
  the budget and reduce operational risk.
- Keep code, commands, migrations, and documentation understandable to a
  beginner with general Python knowledge.
- Explain security implications and verification results when making material
  changes.
- Validate all trust boundaries on the server. Never rely on dashboard controls
  or Android UI restrictions as the sole authorization mechanism.
- Use idempotent APIs for enrollment, synchronization, telemetry, and event
  upload where retries are expected.
- Keep development and production data, devices, credentials, and enrollment
  completely isolated.
- Preserve immutable playlist versions, playback evidence, audit events, and
  assignment history.
- Avoid unrelated refactors or features that do not contribute to the pilot.

## Required Verification

Testing depth must match the risk of each change. At minimum, the completed
pilot requires automated or documented tests for:

- Role authorization and attempts to access driver personal data
- Password reset, session invalidation, rate limiting, and account lockout
- Enrollment expiry, one-time use, replay, revocation, and rooted-device denial
- Kiosk escape resistance and administrator-PIN reset
- Media malware quarantine, spoofed file types, limits, failed decode,
  conversion, audio removal, and preview
- Immutable playlist versioning, scheduling, urgent replacement, configured
  limits, and media-deletion protection
- Interrupted and resumed downloads, corrupt files, atomic playlist activation,
  and rollback to the previous valid playlist
- Offline playback, server-corrected time, delayed event upload, queue limits,
  acknowledgement, and forced-loss reporting
- Per-item completion results, loop batching, duplicate ingestion, crash and
  planned-shutdown or unplanned-interruption recovery, and seven-day report
  finalization
- Device reassignment history, disablement, and explicit reactivation
- Heartbeat, offline, missing-sync, storage, failure, version, temperature, and
  battery, abnormal-app-exit, and device-unavailability alert thresholds
- One-year retention, driver anonymization, immutable records, and CSV privacy
- Backup restoration and development/production isolation
- Physical hardware power, heat, screen-state, kiosk, and startup behavior

Before completing any task, report what changed, what was tested, what could not
be tested, and any remaining security or hardware risk.
