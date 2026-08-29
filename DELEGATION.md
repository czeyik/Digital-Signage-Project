# Production Pilot Delegation

**Last updated:** 2026-08-30 (Asia/Kuala_Lumpur)
**Owner and authority for every role:** Cze Yik

This file is the durable handoff for bringing DUDU production live and launching
the 10-vehicle pilot. Execute the waves in order. One fresh Codex chat owns one
wave from intake through verification; it must not use sub-agents or begin the
next wave.

## Start a wave

Cze Yik should open a fresh chat in this repository and paste:

> Read `AGENTS.md` and `DELEGATION.md`. Execute Wave N only. First inspect the
> current repository and the wave's named sources, then ask me for all missing
> intake as one concise bundle. After I answer, use Codex Goal with the exact
> Wave N goal, work continuously until its exit criteria are evidenced, update
> `DELEGATION.md`, and complete the goal. Do not use sub-agents or start another
> wave.

The wave agent must:

1. Read `AGENTS.md`, this file, and only the canonical documents named by the
   wave.
2. Confirm every prerequisite wave is `Complete`, then revalidate the current
   repository and any live facts relevant to its wave.
3. Ask Cze Yik for the wave's missing intake before creating the goal. Request
   access through an approved vault or local authenticated session; never ask
   for secrets, signing keys, tokens, PINs, driver PII, or recovery codes in
   chat, Git, logs, or this file.
4. After intake, create one goal using the wave's exact **Goal** text. Do not set
   a token budget unless Cze Yik explicitly provides one. If Goal is unavailable,
   ask Cze Yik to enable it instead of silently substituting another workflow.
5. Stay inside the wave's scope. Follow the goal tool's status rules and do not
   mark it complete until every exit criterion has evidence.
6. Before completing the goal, update the Wave register with the status and
   evidence location, update any changed baseline fact, and append one concise
   entry to the Completion log. Do not put sensitive evidence in this file.
7. Stop after the wave. Cze Yik starts the next wave in a new chat.

If a later wave finds a defect that invalidates an earlier completed wave, mark
the current wave `Blocked`, mark the earliest affected wave `Reopened`, record
the reason, and let Cze Yik restart from that wave. Replay every invalidated
downstream gate; do not patch across wave boundaries.

## Fixed decisions

- Release path: GPS-capable Android version code **4 or higher**.
- GPS and self-hosted OpenMapTiles are included in the first pilot.
- The first updater-capable installation is manual. Keep every OTA release
  configuration field disabled throughout this framework unless Cze Yik later
  authorizes a separate OTA release.
- `approved_for_pilot` is an external owner decision by Cze Yik. Code, test
  results, or physical observations must not set or infer it automatically.
- Target production activation and one-device launch window:
  **2026-08-28 11:00 through 2026-08-30 18:00 MYT (UTC+8)**. If it has expired
  before a production mutation, obtain and record a replacement window.
- That window can contain activation and canary start, not necessarily the
  complete 24-hour acceptance period. A canary started inside it can qualify no
  earlier than 2026-08-29 11:00 MYT and no later than
  2026-08-30 18:00 MYT.
- Canary success: the one-device application runs smoothly for 24 consecutive
  hours. There are no additional user-defined quantitative stop
  thresholds. Do not invent any. Existing security, integrity, backup,
  readiness, and fail-closed technical gates still apply. An interruption means
  the 24-hour success condition has not yet been met; after remediation, the
  24-hour clock restarts unless Cze Yik decides otherwise.
- Accepted operating limits: USD 30/month target, 24-hour RPO/RTO, one-host
  failure domain, and local-only host journals.
- Cze Yik fills every personnel and authority role, including owner, budget
  owner, privacy owner, hardware approver, release approver, operator, rollback
  contact, support contact, and pilot lead. Record separately timed approvals
  where the runbook requires separate decisions, even though the person is the
  same.

## Planning baseline — revalidate before relying on it

This is the 2026-08-28 audit snapshot, not standing authorization:

- The Wave 3 APK was built from clean `main`/`origin/main` at
  `901ac09495a20b4a66de01722fa0a9dc5a7e8fd3`; this local handoff records the
  result and has not been pushed. The Wave 2 server candidate is recorded at
  `e0d3f01fc36e9669ed2670d291aa48430cea58bf`.
- Current source contains the updater, GPS, OpenMapTiles, container-policy, and
  hardware-policy changes, with Django migrations through `0015`.
- The production EC2 host and SSM were healthy, but the DUDU stack was stopped
  fail-closed; public ports 80/443 refused connections. Production was still on
  the older 1.0.1/code-2-era release state.
- The latest logical backup was older than the 30-hour freshness gate. Wave 1
  clears the source recovery deadlock with a private, operation-correlated
  refresh that starts only the credential broker and PostgreSQL, then returns
  the host to its stopped state; production remains untouched and any live
  recovery still requires a new reviewed operation and current live gates.
- No current-main production image set, MBTiles installation, reviewed
  Terraform plan/apply, release-config install, migration, or activation has
  been completed. Wave 3's signed GPS APK and release manifest are retained at
  `dspvault/duducar-signing/wave-3-release/`.
- The staged 1.0.2/code-3 APK predates GPS. Wave 2 intake confirms that all
  enrolled devices have been factory-reset and their apps uninstalled; no
  code-2/code-3 identity needs to be retained, and the next installs will use
  code-4+.
- No non-production database copy is available. Wave 2 migration evidence uses
  isolated synthetic SQLite data and does not use production data.
- No Android 12+/API 31 primary-and-spare tablet pair had completed exact-device,
  display, Play Integrity, and GPS qualification.
- AWS secrets and private-bucket structure appeared sound; controlled worker
  failure notification, SMTP delivery, current recovery evidence, content/UAT,
  and external pilot approval remained open.
- Every old SSM command ID, activation operation ID, recovery authorization,
  plan, and dated health result is audit history only. Never resume or reuse it.

The canonical production authority is `docs/production-deployment-runbook.md`.
Where older docs still say GPS is deferred, OTA is staff-sideload-only, a
72-hour canary is sufficient, or name an older commit/artifact, the fixed
decisions above govern and Wave 2 must reconcile the documentation.

## Production gate map

| Wave | Gate cleared | Prerequisite | Status | Evidence / handoff | Note |
|---|---|---|---|---|
| 1 | Stopped-state backup/recovery path | None | Complete | `16cc6fe`; `infrastructure/terraform/ec2/runtime/render-runtime-env`; runtime tests; `docs/production-deployment-runbook.md`; `docs/backup-restore.md` | Renderer now emits exactly 64 raw lowercase hex bytes and normalizes only the known legacy 64-hex-plus-LF token. Failure, retry, receipt-correlation, cleanup, no-public-traffic, runtime-bundle, Terraform, and user-data checks pass; no AWS or production mutation was performed. |
| 2 | Server-side GPS/OpenMapTiles release candidate | Wave 1 | Complete | `e0d3f01` on local `main` (target `origin/main`); backend location/health/retention/map tests; `docs/device-api.md`; `OVERVIEW.md` |
| 3 | Signed GPS Android code-4+ artifact | Wave 2 | Complete | `dspvault/duducar-signing/wave-3-release/manifest/release-manifest.json`; `dspvault/duducar-signing/wave-3-release/checksums/SHA256SUMS` |
| 4 | Exact hardware, GPS, integrity, and privacy qualification | Wave 3 | Complete | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-4-requalification-20260829/`; retained core qualification `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-4-qualification/` | Owner-approved exact current Lenovo `HA259E36` identity, current code-4 install, and device-owner readback recorded. Optional waived observations were not repeated. |
| 5 | Immutable release package, MBTiles, cost, and Terraform plan | Wave 4 | Complete | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-5-release-replay-20260829-r2/manifest/release-manifest.json`; plan `0bf954d82c33ff3dc63157a26e1d5e1ef71c1c785c422215f89728ff2a583c92` | Reissued the applyable plan after a state-serial refresh; current state values and the 0-add/2-change/0-destroy action set are unchanged. No apply or activation was performed. |
| 6 | Accounts, communications, content, and launch approvals | Wave 5 | Complete | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-6-launch-replay-20260829-r2/launch-packet.md`; evidence checksum and revalidation record beside it | Rebound the corrected Wave 5 r2 manifest and exact unapplied plan; current account, DNS/TLS, communications, budget, content, privacy, roster, approvals, window, rollback, and one-device gates remain recorded. No production deployment or enrollment performed. |
| 7 | Production infrastructure recovery and activation | Wave 6 | Complete | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-5-release-replay-20260829-r2/manifest/release-manifest.json`; plan `0bf954d82c33ff3dc63157a26e1d5e1ef71c1c785c422215f89728ff2a583c92`; RECOVER SSM `0b17ff70-2055-4ef7-b8d7-c93b06850141`; final audit SSM `a3f47b8c-993c-4cd1-b7bf-467936cd3a4e`; secure evidence directory `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-7-activation-replay-20260829/evidence/` | Applied the exact reviewed 0-add/2-change/0-destroy plan; installed the pinned runtime and release; verified the existing exact MBTiles; completed failed-existing validation, JIT ARM and RECOVER, migrations through `0015`, readiness, HTTPS health, authenticated map routes, operation-bound backup/receipt, encrypted DLM snapshot, active services/timers, and OTA-disabled state. No tablet was enrolled. |
| 8 | Current isolated recovery proof within 24 hours | Wave 7 | Complete | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-8-recovery-2f2b27eb02698522c37be533d7a7697d/` | Snapshot clone, logical archive, exact media, TLS/loopback, owner dashboard/report/CSV/logout, RPO/RTO, source revalidation, and cleanup checks passed. Temporary resources were destroyed. |
| 9 | Production rehearsal and one-device canary start | Wave 8 | In progress | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-9-rehearsal-20260829/wave9-prerequisite-readback.md`; current WIF worktree | Wave 4 exact-identity requalification is complete. Wave 9 resumed with the owner-authorized AWS–GCP federation repair; no production secret mutation, enrollment, or canary start has occurred yet. |
| 10 | 24-hour canary acceptance and 10-vehicle pilot launch | Wave 9 | Waiting | — |

Allowed statuses are `Waiting`, `Pending`, `In progress`, `Blocked`, `Reopened`,
and `Complete`. Only one wave may be `In progress`.

## Wave 1 — Repair the stopped-state recovery path

**Goal:** Make the stopped `failed-existing` production recovery path capable
of creating and remotely verifying a fresh logical backup without exposing
public traffic, then prove the behavior with tests and an updated runbook.

**Read:** `infrastructure/terraform/ec2/runtime/activate-release`, its runtime
tests, `infrastructure/terraform/release_activation.tf`,
`docs/production-deployment-runbook.md`, and `docs/backup-restore.md`.

**Ask Cze Yik for:** the redacted location of the failed activation and stale
backup evidence; the intended release branch/PR destination; permission to
change the activation/runtime scripts, tests, and runbook; and, only if a
non-production AWS validation is needed, its account access, time window, and
one-off cost limit.

**Work:** Trace the shared activation flow; implement the minimum replay-safe,
operation-correlated backup refresh that keeps Caddy, web, timers, and workers
off and starts only what the backup needs; guarantee cleanup back to the stopped
state; test failure, retry, freshness, remote receipt, and no-public-traffic
boundaries; update the operator sequence. Do not touch production.

**Complete when:** the root-cause test fails on the old flow and passes on the
new flow, all relevant runtime/Terraform checks pass, the documented sequence is
unambiguous, and the clean reviewed commit/evidence is recorded.

## Wave 2 — Freeze the server release candidate

**Goal:** Produce a clean, tested server-side release candidate for the first
GPS/OpenMapTiles pilot, including safe migrations and compatibility behavior
for a code-4+ fleet.

**Read:** `OVERVIEW.md`, `docs/architecture.md`, `docs/device-api.md`,
`docs/openmaptiles.md`, `docs/deployment-readiness.md`, and backend location,
health, retention, map, migration, and test code.

**Ask Cze Yik for:** secure access to a representative sanitized/restored data
copy; the authoritative inventory of enrolled device app versions; confirmation
whether any code-2/code-3 device identity must be retained; and the desired
release branch/PR destination.

**Work:** Reconcile canonical docs with the fixed GPS, OpenMapTiles, manual-OTA,
and 24-hour canary decisions; test or minimally fix location ingestion/health,
30-day retention, privacy boundaries, authenticated map routes, and old-client
behavior; rehearse migrations `0009`–`0015` on the representative copy; run the
relevant backend and migration checks. Do not build/sign Android or mutate AWS.

**Complete when:** no code-2/code-3 client can enter the pilot path unnoticed,
the migration rehearsal and server suites pass, privacy/map behavior is
verified, and a clean release commit is recorded.

## Wave 3 — Build and verify the Android release

**Goal:** Produce and securely retain one production-signed GPS-capable DUDU APK
at version code 4 or higher, with a complete reproducible release manifest.

**Read:** `docs/android-build-verification.md`,
`docs/android-release-signing.md`, `docs/device-api.md`, and `android-player/`.

**Ask Cze Yik for:** the exact version name and version code (minimum 4); the
Wave-2 release commit; secure signing-vault access and backup confirmation;
Play Integrity project/decode access; approved build environment; and secure
APK, mapping, checksum, and evidence destinations.

**Work:** Build from a clean pinned commit; run unit, instrumentation where
available, lint, production/development compile, and release verification;
verify package name, version, minimum API 31, certificate continuity, SHA-256,
byte size, R8 mapping, GPS permissions/behavior, and updater safeguards; retain
artifacts outside Git. Do not enable OTA, enroll a device, or touch production.

**Complete when:** the signed APK and recovery artifacts are independently
verifiable from the recorded manifest, use the approved certificate and
code-4+ identity, and all required Android checks pass.

## Wave 4 — Qualify the physical pilot platform

**Goal:** Qualify the exact primary and spare Android 12+ pilot hardware for
display, kiosk, integrity, GPS, offline replay, and privacy use, and obtain the
separate external owner decision for pilot eligibility.

**Read:** `docs/hardware-qualification.md`, `OVERVIEW.md`, `docs/device-api.md`,
and the Android verification/signing docs.

**Ask Cze Yik for:** physical/ADB access to the primary and spare tablets,
displays, chargers, mounts, SIMs and a safe field-test route; secure Play
Integrity access; the driver-notice text or business facts needed to draft it;
the evidence destination; and availability to make the external
`approved_for_pilot` decision after reviewing evidence.

**Work:** Record exact model, firmware, API, platform/vendor patch, verified
boot, display measurement, and per-device integrity; exercise device owner,
lock task, media, recovery, battery/thermal, GPS cadence/accuracy, permission
denial, mock rejection, offline queue/reconnect, and location-disabled paths;
finalize the driver notice covering purpose, access, and 30-day retention.
Physical observations support but never auto-set `approved_for_pilot`.

**Complete when:** both planned devices have traceable qualification evidence,
the signed code-4+ build passes real integrity and GPS field checks, the notice
is approved, and Cze Yik's separately timestamped external eligibility decision
is recorded. Do not enroll a production device.

## Wave 5 — Prepare the immutable production release

**Goal:** Produce a fully pinned, scanned, costed production release package and
fresh reviewed Terraform plan without applying it or activating production.

**Read:** `infrastructure/README.md`, `docs/openmaptiles.md`,
`docs/aws-cost-estimate.md`, `docs/production-deployment-runbook.md`, and the
Terraform/image definitions.

**Ask Cze Yik for:** AWS account/region and SSO role; protected backend/tfvars
access; ECR and private artifact-store access; the approved Malaysia MBTiles
source, license/custodian and transfer location; the Wave-2 commit and Wave-3
APK manifest; artifact/evidence destinations; and authorization plus cost limit
for builds, scans, uploads, refresh, and planning.

**Work:** Run protected CI/release checks; build and scan immutable ARM64
backend, PostgreSQL, and Caddy images; record their digests; independently
verify and stage the MBTiles extract; securely stage the APK if required; set
the exact required app version while keeping all OTA fields empty/zero; review
state and create a fresh saved Terraform plan; reject unexpected topology,
public access, destructive retention, or projected cost above USD 30/month.

**Complete when:** one manifest binds commit, three image digests, APK identity,
MBTiles checksum, migration set, runtime/release document versions, disabled OTA
values, cost review, CI evidence, and the exact unapplied plan.

## Wave 6 — Close external launch prerequisites

**Goal:** Assemble and verify the complete human, account, communications,
content, privacy, support, and authorization packet required to enter the
production change window.

**Read:** `OVERVIEW.md`, `docs/aws-cost-estimate.md`, the runbook's “Before any
change” and rehearsal sections, and the Wave-4 notice/qualification evidence.

**Ask Cze Yik for:** secure evidence of root/SSO MFA, contacts and budget alerts;
DNS/TLS ownership; SMTP sender plus SPF/DKIM/DMARC and two test inboxes; SNS
subscription endpoint; owner/marketing test accounts; approved pilot media and
rights; the secure driver/vehicle roster; the UAT/support schedule; and a valid
activation/canary window replacing the fixed window if it has expired.

**Work:** Verify contacts and account controls; prove SMTP and SNS subscription
readiness without the deliberate production failure test; prepare approved
media/playlists and the UAT script; bind the notice, qualification, privacy,
support, rollback, budget, and one-device scope into a launch packet; record
Cze Yik's distinct release, cost, privacy, hardware, and change-window
approvals. Do not deploy application code or enroll a device.

**Complete when:** every runbook owner prerequisite has current evidence, the
content/UAT/roster/support packet is ready, and a still-valid production window
and rollback authority are recorded.

## Wave 7 — Recover and activate production

**Goal:** Apply the exact reviewed release and recover the stopped host through
the supported SSM activation path until the pinned GPS/OpenMapTiles production
stack is live, ready, backed up, and OTA-disabled.

**Read:** the complete production runbook, `infrastructure/README.md`,
`docs/deployment-readiness.md`, Wave-1 recovery changes, and the Wave-5 manifest.

**Ask Cze Yik for before creating the goal:** a valid maintenance window; AWS
SSO and approved vault access; the reviewed plan/manifest/evidence locations;
current change, cost, rollback, and operator authorization; and availability
to issue the exact just-in-time confirmations. Never collect the confirmations
in advance.

**Work:** Recheck live state, backup age, snapshot, drift, secrets schema, and
plan freshness; obtain separate approval before applying the exact plan;
transfer verified MBTiles and install the pinned runtime/release config through
SSM only; use the Wave-1 stopped-state backup refresh; create a new operation
and command IDs; validate, obtain the exact ARM and RECOVER confirmations at
their decision points, and run the sole supported `failed-existing` activation
path; migrate through `0015`; verify digests, version, units, timers, public
readiness, map data, fresh remote backup/receipt, and completed DLM snapshot.

**Complete when:** production is live on the exact manifest, migrations and
readiness pass, public routes and authenticated maps work, backups are current,
all required units/timers are active, no secret leaked, and every OTA field is
still disabled. Do not enroll a tablet.

## Wave 8 — Prove current recovery

**Goal:** Demonstrate isolated logical, snapshot, and exact-media recovery from
the newly activated production release within the accepted 24-hour RPO/RTO and
destroy all temporary recovery resources.

**Read:** `docs/backup-restore.md`, `infrastructure/recovery-smoke/README.md`,
the production runbook recovery gate, the Wave-5 manifest, and Wave-7 backup
and snapshot evidence.

**Ask Cze Yik for:** AWS SSO access; the authorized current logical archive,
DLM snapshot, media versions, and expected records; isolated test credentials;
temporary-resource region/window/cost approval; secure evidence destination;
and teardown authorization.

**Work:** Confirm the selected sources satisfy the 24-hour RPO; create a fresh
operation; restore the logical archive and DLM clone in isolation using the
pinned release; verify exact media, owner login, readiness, reports and CSV
without fabricated data; measure RTO; capture non-secret evidence; and destroy
the isolated stack. Never reuse an old operation or production identity and
never route recovery through production.

**Complete when:** source freshness, all three recovery layers, and evidence
checks pass within 24 hours, cleanup is independently verified, and no temporary
billable resource remains.

## Wave 9 — Rehearse production and start the canary

**Goal:** Pass the production rehearsal, manually install and enroll exactly
one externally approved code-4+ tablet, and establish the timestamped start of
the 24-hour canary.

**Read:** the runbook's hardware/rehearsal/canary sections,
`docs/deployment-readiness.md`, `docs/hardware-qualification.md`, and the Wave-3,
Wave-4, Wave-7, and Wave-8 evidence.

**Ask Cze Yik for:** secure access to production owner/marketing accounts and
two test inboxes; the approved content/UAT package; the selected qualified
tablet and signed APK; secure driver/vehicle assignment data; authorization for
the deliberate isolated-worker failure; availability to set/confirm the
external `approved_for_pilot` gate; and the exact one-device enrollment window.

**Work:** Verify DNS/TLS, authorization/privacy, private media, processing,
playlist, backup/alarms/budget, OpenMapTiles and GPS endpoints; deliberately
fail one isolated worker and prove EventBridge/SNS delivery; require Cze Yik's
external approval; perform clean setup/device-owner assignment and the first
manual updater-capable APK install; prove integrity, enroll only that device,
exercise playback/sync/offline/evidence/location/map behavior, reconfirm OTA is
disabled, and record the canary start time and baseline.

**Complete when:** every rehearsal gate passes, exactly one approved device is
healthy in production with GPS and content working, alert delivery is proven,
OTA remains disabled, and the 24-hour observation interval has an explicit
MYT start timestamp.

## Wave 10 — Accept the canary and launch the fleet

**Goal:** Accumulate 24 consecutive hours of smooth one-device production
operation, obtain Cze Yik's separate expansion approval, and launch the
remaining qualified devices up to the 10-vehicle pilot scope.

**Read:** Wave-9 baseline, production runbook decision rules, dashboard/alert
evidence, and the current release manifest. Keep this goal narrowly limited to
monitoring, acceptance, and approved expansion.

**Ask Cze Yik for:** monitoring/dashboard/log access; the observation checkpoint
and support schedule; physical access to the canary; secure details and current
qualification evidence for the remaining devices/assignments; and availability
to issue expansion approval only after the 24-hour evidence is complete.

**Work:** Keep one goal active across the observation period and record
checkpoint evidence for application availability, heartbeats, sync/playback,
proof, GPS ingestion/map, alerts, backups, worker behavior, and cost. Do not
invent stop thresholds. If operation is interrupted, preserve evidence,
remediate only within the current approved release/runbook, and restart the
24-hour clock; a source, artifact, hardware, or infrastructure change reopens
the corresponding earlier wave. After 24 hours pass, obtain a separate Cze Yik
decision, then manually install, externally approve, integrity-check, enroll,
and smoke-test remaining qualified devices one at a time, never exceeding 10
total. Keep OTA disabled.

**Complete when:** the timestamped record proves 24 consecutive smooth hours,
Cze Yik has approved expansion, every launched device is qualified and healthy,
the fleet count is at most 10, production gates remain green, and the pilot
launch record and support handoff are complete.

## Completion log

Append one line per status change. Keep evidence in its secure store and link
only a non-sensitive path or identifier here.

| Timestamp (MYT) | Wave | Status | Commit/release | Evidence | Note / next input |
|---|---|---|---|---|---|
| 2026-08-28 | Framework | Complete | `4be6e7a0cdef` baseline | `DELEGATION.md` | Wave 1 is next. |
| 2026-08-28 12:35 | 1 | Complete | `9bb2c5f` | `infrastructure/terraform/ec2/runtime/test-runtime-guardrails.sh` and the Wave 1 register above | Source-only recovery repair verified; no production or external evidence used. |
| 2026-08-28 13:39 | 2 | Complete | `e0d3f01` | Wave 2 register above; backend test suite and isolated migration rehearsal | 268 passed, 2 skipped; no non-production copy was available, so synthetic local data was used; prior devices were reset/apps removed and no code-2/code-3 identity is retained. No AWS mutation or Android build/signing was performed. |
| 2026-08-28 14:27 | 3 | Complete | `4.0.1` / code `4` from `901ac09` | Wave 3 register above; signed APK, R8 mapping, checksum, and reproducible manifest in the approved vault | Development unit/lint/build/instrumentation compilation and production compile/R8/lint/signature checks passed; OTA remained disabled and no device or production system was touched. |
| 2026-08-28 16:07 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Primary and spare are identified and provisioned; display measurement, field GPS, decoded Integrity verdicts, media/offline field exercise, notice approval, and owner eligibility value remain open. |
| 2026-08-28 16:09 | 4 | Blocked | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Awaiting physical measurements, open-sky GPS, scoped Integrity decoding, operator field observations, notice approval, and the explicit `approved_for_pilot` value. |
| 2026-08-28 16:34 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Physical glass measurements and the explicit owner approval were supplied; open-sky GPS, decoded Integrity verdicts, and media/offline/power field observations remain open. |
| 2026-08-28 18:06 | 4 | In progress | `4.0.1-diagnostic` / code `5` temporary same-certificate field build | Wave 4 secure qualification index in the vault | Disposable GPS diagnostic staged on both tablets for an offline open-sky run; driver notice approved at 18:06:43 MYT; exact code-4 release must be restored afterward. |
| 2026-08-28 18:31 | 4 | In progress | `4.0.1-diagnostic` / code `5` temporary same-certificate field build | Wave 4 secure qualification index in the vault | Operator reports the open-sky GPS procedure passed on both. Spare exact readback recorded: GPS, mock false, 20.693 m, location `2026-08-28T10:28:23.252Z`, received `2026-08-28T10:28:34.942633Z` UTC; spare restored to code 4. Primary ADB is unauthorized, so exact readback and code-4 restoration remain pending. |
| 2026-08-28 19:03 | 4 | In progress | `4.0.1-diagnostic` / code `5` temporary same-certificate field build | Wave 4 secure qualification index in the vault | User-authorized factory reset cleared the primary diagnostic result, DUDU package data, and device-owner state; post-reset identity is Android 15/API 35, firmware `ZUI_17.0.31.023`. Diagnostic was restaged with clean data and granted location permissions; post-reset GPS run, code-4 restoration, device-owner re-provisioning, and kiosk rechecks remain pending. |
| 2026-08-28 19:11 | 4 | In progress | `4.0.1-diagnostic` / code `5` temporary same-certificate field build | Wave 4 secure qualification index in the vault | Primary post-reset GPS field procedure operator-confirmed passed. GNSS corroboration showed repeated GPS deliveries, final horizontal accuracy `3.7 m`, and `13` satellites. The diagnostic's final saved XML was a later network fix, so exact GPS display timestamps were not retained; code-4 restoration, device-owner re-provisioning, and remaining Wave 4 gates are still pending. |
| 2026-08-28 19:12 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Primary disposable diagnostic was cleared and removed after the post-reset field pass; exact code-4 APK hash/bytes verified, device owner re-established, and lock-task state verified `LOCKED` with MainActivity focused. Visible shutdown/API rechecks, fresh current Integrity/decode, media/offline, and battery/thermal gates remain open. |
| 2026-08-28 19:21 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Correct Play Integrity Cloud project confirmed as project ID `healthy-wares-506910-g5`, project number `552923442234`; Play Integrity API is enabled. Historical Wave 3 records retain `132918389760` as their original build input; the fresh probe/decode must use the corrected project. |
| 2026-08-28 19:27 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Fresh post-reset signed-certificate Standard Integrity probe passed with project number `552923442234`; the token was not retained. REST decode reached Google but the active user credential returned `ACCESS_TOKEN_SCOPE_INSUFFICIENT`; a playintegrity-scoped decoder credential remains required. Exact release, owner, and lock-task state were restored afterward. |
| 2026-08-28 19:35 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Fresh post-reset token decoded successfully with the service-account-derived `playintegrity` scope. Request package/hash matched; device verdict was `MEETS_DEVICE_INTEGRITY`; app verdict was `UNRECOGNIZED_VERSION`, licensing `UNEVALUATED`, code `4`, and the expected signing certificate digest was returned. The temporary target was removed and the exact release, owner, and lock-task state were restored. |
| 2026-08-28 22:39 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Fresh HONOR spare token acquired and decoded successfully with project number `552923442234`. Request package/hash matched; device verdict was `MEETS_DEVICE_INTEGRITY`; app verdict was `UNRECOGNIZED_VERSION`, licensing `UNEVALUATED`, code `4`, and the expected signing certificate digest was returned. Temporary target/test package and token were removed; exact release, Device Owner, and lock-task state were restored and verified. |
| 2026-08-29 00:29 | 4 | In progress | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Primary Lenovo short media qualification passed: validated 1920x1080 image and H.264 video completed offline loops, queued with `captured_offline=true`, and drained through a localhost-only reconnect endpoint. Fixture, credentials, and reverse port were removed; exact code-4 hash, Device Owner, and locked-task state were restored. HONOR fixture cleanup awaits ADB reauthorization; extended 12-hour, battery/runtime, thermal-under-load, and physical power observations remain open. |
| 2026-08-29 00:41 | 4 | Closed by owner direction; remaining gates waived | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Owner directed that Wave 4 stop here and that no further qualification be performed. The final disposable run left the DUDU kiosk in device-owner lock-task with the RSA prompt not visually accessible; Safe Mode did not provide an exit. The operator will factory-reset Lenovo `HA259E36` and HONOR `AKWJ9X4B09G02667` before handoff. Next agent must verify both resets and treat prior app, Device Owner, credentials, fixtures, and kiosk state as invalid. Wave 5 was not started in this chat. |
| 2026-08-29 00:59 | 4 | Complete | `4.0.1` / code `4` from `901ac09` | Wave 4 secure qualification index in the vault | Owner confirmed Wave 4 complete and final; remaining qualification work is intentionally skipped. Wave 5 may begin. |
| 2026-08-29 02:29 | 5 | Complete | `6cb57fe` / `4.0.1` code `4` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-5-release/manifest/release-manifest.json` | Geofabrik MBTiles, corrected signed APK, clean ARM64 ECR digests/scans, cost review, and fresh unapplied Terraform plan are checksum-bound in the secure vault; no production apply or activation performed. |
| 2026-08-29 03:15 | 6 | In progress | `6cb57fe` / `4.0.1` code `4` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-6-launch/launch-packet.md` | Owner intake recorded; SMTP submission passed. Secure roster now has one valid assignment row; the requested SNS endpoint differs from the existing subscription and reviewed Wave 5 configuration. No production deployment or enrollment performed. |
| 2026-08-29 03:20 | 6 | Blocked | `6cb57fe` / `4.0.1` code `4` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-6-launch/launch-packet.md` | Roster and content packet are ready. Completion awaits owner direction to reconcile the requested SNS endpoint with the immutable Wave 5 plan and confirmation that both SMTP test inboxes received the message. No production deployment or enrollment performed. |
| 2026-08-29 16:28 | 6 | Complete | `6cb57fe` / `4.0.1` code `4` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-6-launch/launch-packet.md` | Owner retained the confirmed `support@duducar.co` SNS subscription, confirmed both SMTP inboxes received the readiness message, and confirmed the fixed window and five approvals. Roster, normalized media, privacy notice, support/UAT script, and one-device scope are bound in the packet. No production deployment or enrollment performed. |
| 2026-08-29 17:00 | 1 | Reopened | `6cb57fe` / `4.0.1` code `4` | `infrastructure/terraform/ec2/runtime/render-runtime-env`; SSM `a74b5117-2e97-4fdd-9b9c-4fecaed16681`; SSM `3d565a58-1269-4dd0-bc23-e2ea6fe4b5f0` | Wave 7 production validation proved the renderer's bearer-token newline defect in the stopped-state backup runner. Wave 1 must repair and reverify it. |
| 2026-08-29 17:00 | 5 | Reopened | `6cb57fe` / `4.0.1` code `4` | Wave 5 release manifest in the secure vault | The immutable runtime bundle is invalidated by the Wave 1 defect; rebuild the release package and fresh plan after repair. |
| 2026-08-29 17:00 | 6 | Reopened | `6cb57fe` / `4.0.1` code `4` | Wave 6 launch packet in the secure vault | The packet's release binding is invalidated by Wave 5 reopening; replay the launch gate after the corrected release. |
| 2026-08-29 17:00 | 7 | Blocked | `6cb57fe` / `4.0.1` code `4`; operation `54eb404f02af1eaf3545d97024395462` | SSM `a74b5117-2e97-4fdd-9b9c-4fecaed16681`; SSM `3d565a58-1269-4dd0-bc23-e2ea6fe4b5f0` | Terraform applied and runtime/release/map staging completed, but activation stopped before deployment on the token defect. No public stack was activated and no tablet was enrolled. Restart from Wave 1; no cross-wave patch was made. |
| 2026-08-29 17:15 | 1 | Complete | `16cc6fe` on local `main` | Runtime guardrail suite; root-only runtime-bundle/config contracts; Terraform validation; user-data size check; `docs/production-deployment-runbook.md`; `docs/backup-restore.md` | Repaired the reviewed token-format defect, proved the old renderer fails the new regression contract, and retained the stopped-state recovery/cleanup behavior. No AWS or production mutation was performed. Wave 5 may replay. |
| 2026-08-29 17:16 | 5 | In progress | `16cc6fe` on local `main` | Wave 5 release package and Terraform plan in the approved vault | Wave 1 is repaired; replay will rebind the runtime bundle and fresh plan without applying or activating production. |
| 2026-08-29 17:23 | 5 | Complete | `e3a522414fe0165ddec47579420572b6b4dd4a6e` on local `main` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-5-release-replay-20260829/manifest/release-manifest.json`; plan `210f353f60869f4063bd50e076c93a5ebc9a8a17b840fd9816a263408a273e39` | Immutable replay package and fresh reviewed plan are checksum-verified; only expected SSM document updates remain, with no production apply or activation. Wave 6 may replay. |
| 2026-08-29 17:29 | 6 | Complete | `e3a522414fe0165ddec47579420572b6b4dd4a6e` / `4.0.1` code `4` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-6-launch-replay-20260829/launch-packet.md`; `evidence/wave6-revalidation.json`; packet SHA-256 `91a1d320de482ce3ee325ef01e3acc712b82086bef9fec54192d30c8fe7e9f03` | Replayed the launch gate against the corrected manifest and plan. AWS account controls, DNS/TLS, SMTP/SNS, budgets, approved content, privacy notice, Wave 4 qualification, roster, approvals, exact `16:00–23:59 MYT` window, and rollback authority are current. No production deployment or enrollment performed. Wave 7 may begin. |
| 2026-08-29 17:32 | 7 | In progress | `e3a522414fe0165ddec47579420572b6b4dd4a6e` / plan `210f353f60869f4063bd50e076c93a5ebc9a8a17b840fd9816a263408a273e39` | Wave 5 replay package; Wave 6 replay packet; prior failed SSM/operation IDs retained as audit only | Wave 7 replay started under the approved `2026-08-29 16:00–23:59 MYT` window. No new production mutation or just-in-time activation confirmation has been issued yet. |
| 2026-08-29 17:38 | 5 | Complete | `e3a522414fe0165ddec47579420572b6b4dd4a6e` / plan `0bf954d82c33ff3dc63157a26e1d5e1ef71c1c785c422215f89728ff2a583c92` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-5-release-replay-20260829-r2/manifest/release-manifest.json`; package checksums | The r1 binary plan became stale after a Terraform state refresh; state values matched, and the r2 plan retained the identical 0-add/2-change/0-destroy action set. No release input changed and no apply occurred. |
| 2026-08-29 17:39 | 6 | Complete | `e3a522414fe0165ddec47579420572b6b4dd4a6e` / `4.0.1` code `4` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-6-launch-replay-20260829-r2/launch-packet.md`; packet SHA-256 `790ccc2af3669c54f00ad1fc3b54e54e5ca9ab3802f4d7de17144e4b6d998edf` | Rebound the complete launch packet to the applyable r2 plan and reverified its evidence. No production deployment or enrollment performed. |
| 2026-08-29 18:12 | 7 | Complete | `16cc6fe` / r2 plan `0bf954d82c33ff3dc63157a26e1d5e1ef71c1c785c422215f89728ff2a583c92` / operation `ede8b7d4d9bf0c3fa3e322499b0af05f` | RECOVER SSM `0b17ff70-2055-4ef7-b8d7-c93b06850141`; final audit SSM `a3f47b8c-993c-4cd1-b7bf-467936cd3a4e`; secure Wave 7 evidence directory | Exact reviewed plan applied successfully. The recovered stack is live and ready with pinned images, GPS/OpenMapTiles routes, active services/timers, current operation-bound backup, encrypted DLM snapshot, and OTA disabled. No tablet was enrolled. Wave 8 remains waiting. |
| 2026-08-29 18:58 | 8 | Complete | `1d6497a` / operation `2f2b27eb02698522c37be533d7a7697d` | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-8-recovery-2f2b27eb02698522c37be533d7a7697d/` | Snapshot and logical restore, exact media, TLS/loopback, owner dashboard/report/CSV/logout, RPO/RTO, source revalidation, and independent cleanup all passed. Recovery helper role initialization fix is committed. Wave 9 may begin. |
| 2026-08-29 19:11 | 4 | Reopened | `4.0.1` / code `4`; current device readback | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-9-rehearsal-20260829/wave9-prerequisite-readback.md` | Wave 9 readback found Lenovo `HA259E36` on firmware/build `...17.0.31.259_ST_251031` with security patch `2025-09-05`, versus the immutable Wave 4 record `...17.0.31.023_ST_250320` and `2025-02-05`. Fresh exact-identity qualification is required; no production mutation occurred. |
| 2026-08-29 19:11 | 9 | Blocked | `3d77340` / current Wave 5 r2 package | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-9-rehearsal-20260829/wave9-prerequisite-readback.md` | Production rehearsal stopped before the authorized worker failure, APK install, device-owner provisioning, enrollment, and canary start because the selected tablet no longer matches Wave 4's exact firmware/build and security-patch identity. |
| 2026-08-29 19:24 | 4 | In progress | current Lenovo `TB311XU` / Android 15 / code-4 release | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-4-requalification-20260829/` | Owner directed a minimal fresh exact-identity record for current firmware/build and security patch; retained 10.05-inch display evidence is used and all optional waived exercises remain excluded. |
| 2026-08-29 19:29 | 4 | Complete | current Lenovo `TB311XU` / Android 15 / code-4 release | `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-4-requalification-20260829/` | Owner approval persisted as production qualification record `1`; exact artifact, install hash, device owner, and alarm-policy readbacks pass. Optional waived exercises were not repeated. |
| 2026-08-29 20:59 | 9 | Blocked | current code-4 release | SSM `2a37c043-eb79-4d9d-8669-ce2a0c478a2e`; final state readback `6713f701-0bb6-4351-8dc9-a409a31bff66` | Packet reassembly found the real enrollment request and token, but the live decoder rejected it because all stored production secret versions retain the obsolete Play Integrity identity. Lenovo `HA259E36` remains pending with zero active credentials; no canary was started. Restore the approved decoder key or its GCP IAM access before retrying. Optional waived exercises were not repeated. |
| 2026-08-30 01:18 | 9 | In progress | current code-4 release | AWS SSO caller verification; approved GCP project/service-account access and impersonation verification | Owner extended the production and maintenance window through `2026-08-30 06:00 MYT`; AWS and GCP access now pass. Decoder key creation remains blocked by the enforced service-account-key policy, so no production mutation or canary start has occurred. |
| 2026-08-30 02:18 | 9 | In progress | current code-4 release; AWS–GCP WIF | GCP WIF pool/provider and backend federation implementation | Owner extended the production and maintenance window through `2026-08-30 18:00 MYT` and selected option 3. The dedicated AWS provider is restricted to the production application role; static-key creation is no longer required. No production secret mutation, enrollment, or canary start has occurred. |
