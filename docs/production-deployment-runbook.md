# Production Change and One-Device Canary

This is the release gate for the live pilot: one ARM64 `t4g.small` in
`ap-southeast-5` runs Caddy, Django, and PostgreSQL under systemd. Media is
private S3 through CloudFront signed URLs; Fargate is an isolated on-demand
worker. Legacy ECS web/ALB/RDS are removed and are not rollback paths. Follow
`infrastructure/README.md` for Terraform and runtime-asset commands; this page
defines the release order, release-config operation, and stop conditions. A
broad authorization never permits an unexpected plan.

## Before any change

1. Obtain a maintenance-window approval naming the reviewed, CI-protected clean
   commit, operator, change/cost limit, rollback contact, and one-device scope.
2. Owner confirms root MFA/no root keys, MFA SSO, billing contacts and USD 30
   budget alerts; production AWS credentials/region; DNS/TLS; SMTP sender,
   SPF/DKIM/DMARC and two test inboxes; Play Integrity project/decode account;
   protected release key/vault/offline backup; two planned canary tablets; and canary contacts/window.
3. Require a final signed APK and matching `required_app_version`; its version
   name, version code, package, checksum and signing certificate fingerprint
   must be recorded. Never send signing or service secrets through Git/chat.
4. Require an exact Android 12+ model/firmware/security-patch registration with
   a 9.00–12.00-inch attested display measurement and real signed-APK
   `MEETS_DEVICE_INTEGRITY`; enrollment stays disabled if either identity or
   integrity is absent. Physical pass fields and evidence are optional
   observations under the simplified policy.
5. Review `docs/aws-cost-estimate.md`; stop if projected monthly cost exceeds
   USD 30 excluding per-tablet mobile data, unless the owner changes the target.

### CI service-unavailability exception

A GitHub Actions quota or service outage may replace the required hosted CI run
for one release only when the owner explicitly approves that exception before
the release commit is created. This does not permit disabling or changing branch
protection. Push the clean commit to a dedicated remote release branch, record
its full hash, and merge that same change through the normal protected path when
hosted CI is available again and before the next production release.

The operator must reproduce every applicable workflow job locally from the
exact release commit. For a backend-only release this includes Ruff, Django
checks and migration detection, the complete SQLite and PostgreSQL test suites,
the worker-root and Docker-context checks, dependency auditing, an ARM64 backend
image build and runtime check, the no-package-installer assertion, and a pinned
Trivy scan that rejects every unresolved HIGH or CRITICAL finding. Run any
additional workflow job affected by the diff. Retain the commands, versions,
results, image ID, image digest, vulnerability database timestamp, reviewer,
approval, and exception reason in the production change record.

The exception replaces only unavailable hosted-CI evidence. It does not waive
review, a clean immutable commit, registry digest pinning, backups and snapshot
freshness, Terraform plan review, SSM validation, the operation-specific install
and activation confirmations, post-deploy checks, or rollback controls.

## Prepare a release

1. Start only from a clean reviewed release worktree. Run applicable backend,
   Android, Terraform and security tests; record results and unresolved risk.
2. Build Linux/ARM64 backend from that commit; scan it and reject unresolved
   HIGH/CRITICAL findings. Push the approved image and select its immutable ECR
   digest—never a tag. If worker code changes, its task definition uses this
   same digest and required IAM (including host `ecs:ListTasks`) before web code.
3. For an OTA release, upload the final same-key APK to the private media bucket
   under its reviewed `updates/*.apk` key and independently verify the object
   checksum and byte size before enabling the matching Terraform `app_update_*`
   values. Keep the rollout percentage at the approved one-device scope until
   the canary is healthy.
4. Create and verify a fresh versioned logical backup and a completed DLM data
   snapshot; record exact versions and source. Ensure no isolated worker task
   runs before schema/worker changes.
5. Make a fresh saved Terraform plan using protected production variables.
   Review every action: expected controls retain EC2/private CloudFront/no
   continuous worker and no ECS web/ALB/RDS/NAT/public S3. Explicitly approve
   any backup lifecycle retention/deletion effect. Stop on unexpected changes.
6. Apply the reviewed Terraform plan before host configuration. Never put
   secrets in Terraform, arguments, history, logs or chat; enter them only via
   the approved Secrets Manager path.

## Configure and deploy through SSM

1. Use SSM only; never SSH or hand-edit `/etc/duducar/release.env`, cloud-init,
   or runtime files. From applied Terraform outputs obtain the instance and the
   exact runtime-bundle, release-config, and release-activation document names,
   versions, hashes, three image digests, and required app version. The app
   version must match the signed APK. The application secret must already have
   `WORKER_DB_PASSWORD` and comma-separated canonical lowercase 64-hex
   `PLAY_INTEGRITY_APP_CERTIFICATE_SHA256`; never record their values.
   The runtime renderer must leave `aws-credentials-token` as exactly 64
   lowercase hexadecimal bytes with no line ending; it normalizes only the
   known legacy 64-hex-plus-LF value and refuses other existing forms.
2. Generate one fresh 32-hex operation ID. Send the pinned runtime document with
   `Mode=validate`, the clean 40-hex commit, operation ID, and exact Caddy digest.
   Review its complete staged manifest, then send `Mode=install` with identical
   inputs. It installs/backs up every runtime script, unit, timer, PostgreSQL
   rule/grant, broker, verifier, and Caddyfile, runs `daemon-reload`, and does not
   activate anything. Record both command IDs and require `Success`.
3. Validate then install the pinned release-config document with the same commit
   and operation ID and exact `BackendImage`, `PostgresImage`, `CaddyImage`, and
   `RequiredAppVersion`. Include the exact `AppUpdateVersionCode`,
   `AppUpdateVersionName`, `AppUpdateStorageName`, `AppUpdateSha256`,
   `AppUpdateSizeBytes`, and `AppUpdateRolloutPercent` values when overriding
   the document defaults. It atomically owns all release selections. A same-input
   config rollback is allowed only before activation/migrations; runtime-bundle
   rollback likewise uses its original document/operation and is forbidden once
   activation starts.
4. Before activation, prove an active owner can log in, record worker/schema
   compatibility, and complete the policy-specific user/device notices. Send
   the pinned activation document first with `Mode=validate`, empty
   `Confirmation`, `ActivationKind=existing`, and the exact commit, operation,
   images, and app version. Require `Success` and review its installed-manifest,
   release-file, and secret-schema checks. `initial-empty` is allowed only for a
   genuinely empty PostgreSQL directory. `failed-existing` is an exceptional
   recovery type only after retained SSM evidence has been independently
   reviewed to show that a prior activation failed before deploy and left every
   DUDU unit/container stopped. Its
   `RecoveryFromOperationId` and `FailedActivationCommandId` must record that
   operator-supplied audit correlation; the command ID is not host-verifiable
   proof. The failed-existing preflight first checks the stopped state and the
   operation-correlated remote receipt. If that receipt is stale, missing, or
   belongs to another operation, it starts only the credential broker and
   PostgreSQL, runs a private one-shot backend backup runner with no published
   ports, then stops and verifies cleanup. Caddy, the web service, timers, and
   workers remain off throughout. Validation repeats the remote-backup, disk,
   memory, and snapshot gates while omitting only the impossible public HTTPS
   probe; it also sends and clears an operation-scoped Operations SNS alert to
   prove alert publication. Temporary backup components are cleaned up before
   validation succeeds; no production stack is activated.
   Then use `Mode=arm-failed-existing` with the exact phrase
   `ARM <new-operation-id> FROM <failed-operation-id>`; it repeats those gates
   and writes a root-only authorization valid for 15 minutes, without leaving
   any service or container running.
5. Only after a fresh approval type exactly `ACTIVATE <operation-id>` and send a
   separate activation command with `Mode=activate` and that exact
   `Confirmation`; every other input and the document version/hash must be
   unchanged. A reviewed `failed-existing` recovery instead requires the
   distinct exact phrase `RECOVER <new-operation-id> FROM <failed-operation-id>`
   after the separate arm step; it repeats its stopped-state and non-public
   preflight immediately before atomically consuming that authorization. This is
   the sole deploy path: it verifies remote backup, completed DLM snapshot and
   host health,
   stops timers/systemd, starts the scoped broker, deploys with public Caddy
   absent, runs migrate → grant-runtime → migration-check, then starts
   systemd/timers and asserts readiness, running image
   digests, effective app version, active units, and a new verified remote
   backup. Record output and command ID. A failure after shutdown keeps traffic
   stopped; do not improvise a manual deploy or reverse a migration. Use a
   reviewed forward fix after a migration has begun.

## Hardware, rehearsal, and canary

1. An owner must approve the exact model/firmware/security-patch
   `HardwareQualification` with a compatible display measurement before
   assignment. Physical pass fields and test evidence are optional observations
   under the simplified policy; legacy vehicle-power fields never qualify a
   device.
2. From outside the host, verify DNS/TLS/redirects/live+ready. Test owner and
   marketing authorization, logout/lockout/reset, driver-name privacy, private
   signed-media denial, JPEG/PNG/MP4 quarantine/normalization/preview, playlist
   behavior, isolated-worker exit, timers/host alarms/backup alerts and budget.
3. Deliberately fail one isolated worker task and require EventBridge/SNS
   delivery to a test inbox. A configured rule is not proof of notification.
4. On exactly one assigned tablet: enroll using a 15-minute code; optionally
   exercise atomic sync/hash/playback/fallback, idempotent proof/report/CSV
   privacy, offline and corrupt-download recovery, server-time schedule-boundary
   replacement, disable/reactivate/re-enroll, cache/queue behavior, remote Admin
   mode from playback/fallback/maintenance/enrollment, battery, admin-only
   shutdown, app exit, and physical recovery. Confirm the normal playback UI
   has no shutdown or local administrator gesture.
5. Confirm only full media counts once; a battery/parked play proves neither
   vehicle operation, occupancy, external power nor audience. Run Android-13
   exit diagnostics where applicable and document platform limitations.

## Recovery and decision

Before canary, `backup-restore.md` must show an isolated logical archive,
DLM-clone and exact-media restore within 24 hours, with owner login and report/
CSV export evidence. Header-only CSV is export-path evidence only when the
selected source genuinely has zero playback events; never fabricate records.

**Go** only when CI, image review, owner prerequisites, private media,
notification delivery, restore evidence, cost, integrity and exact-device
identity/display gates pass. Otherwise keep vehicle enrollment disabled;
dashboard/API-only operation is the fallback. Expand beyond one device only with
separate approval.
