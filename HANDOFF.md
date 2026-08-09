# Digital Signage Project — Deployment Handoff

**Updated:** 2026-08-09 Asia/Kuala_Lumpur

**Scope:** controlled live launch of the 10-vehicle Malaysia pilot.
**Authoritative operating constraints:** `AGENTS.md` and `docs/architecture.md`.

## Mission and current position

The project is preparing a pre-canary release for the production pilot. The
application, battery-policy, and recovery-control changes are merged to
`main`, but **production has not been deployed from those changes**. Do not
mistake a Git merge, passing CI, or an isolated recovery control-path test for
a completed go-live.

The next agent should preserve the pilot topology and its cost/safety limits:

- Production is one ARM64 `t4g.small` EC2 host in `ap-southeast-5`, running
  Caddy, Django, and PostgreSQL containers.  It is accessed through SSM; SSH is
  not exposed.
- Media processing is an isolated, bounded ARM Fargate task.  It is not a
  continuously running worker.
- The USD 30/month target, private S3/CloudFront delivery, and 24-hour RPO/RTO
  remain non-negotiable pilot constraints.

## Source-control and CI handoff

| Item | Verified state |
| --- | --- |
| Original release hardening | [#25](https://github.com/czeyik/Digital-Signage-Project/pull/25), merge `8a7ca5eabf8552c6b997be7ea81b5e28a38fd185` (source `c938d7c234aa697ea9fbeea5d6f7891c440cdd03`) |
| Battery-policy merges | [#26](https://github.com/czeyik/Digital-Signage-Project/pull/26) `05cbf9d`, [#27](https://github.com/czeyik/Digital-Signage-Project/pull/27) `f99a941`, and [#28](https://github.com/czeyik/Digital-Signage-Project/pull/28) `6d2f3b3` |
| Recovery-control merges | [#29](https://github.com/czeyik/Digital-Signage-Project/pull/29) `b29d6e7`; direct commit `a328c5e` (XFS journal replay); [#31](https://github.com/czeyik/Digital-Signage-Project/pull/31) `53a923c`; [#32](https://github.com/czeyik/Digital-Signage-Project/pull/32) `ca5e527`; [#33](https://github.com/czeyik/Digital-Signage-Project/pull/33) `b4e7021`; [#34](https://github.com/czeyik/Digital-Signage-Project/pull/34) `80b24f9`; [#35](https://github.com/czeyik/Digital-Signage-Project/pull/35) `3b58c0f`; [#36](https://github.com/czeyik/Digital-Signage-Project/pull/36) `3b9ba95`; [#37](https://github.com/czeyik/Digital-Signage-Project/pull/37) `9851366` |
| Reviewed release baseline | `origin/main` before this documentation update = `756b81641944eaa5b726a75802c44dcff221e4b3` — [#38](https://github.com/czeyik/Digital-Signage-Project/pull/38), `Harden backend Docker build context` |
| CI evidence | The merged hardening runs passed backend, PostgreSQL-backed backend tests, Android, Terraform, and ARM64 container build/runtime/Trivy. [#38](https://github.com/czeyik/Digital-Signage-Project/pull/38) additionally passed all five required checks after exercising Docker's native build-context exclusion fixture. |

Before making a further change, inspect the worktree, fetch, and deliberately
choose the intended branch. This handoff is release-state documentation, not
deployment authority.

PR #38 is source/CI hardening only; it did not build, push, or deploy a
production artifact.

## Decisions and approvals already made

- The user authorized the pre-canary code fixes and the temporary cost of an
  isolated restore rehearsal.
- The user approved changing S3 **noncurrent** logical-database-backup versions
  from 30 days to **1 day**.  This is recorded in `docs/backup-restore.md` and
  Terraform, but has **not** been applied to AWS.
- No owner-login proof has been recorded for the current isolated recovery
  drill. A historic account-owner inspection is not a replacement for the
  required restored-environment owner-login and protected-dashboard check.
- The next battery-policy Android release is **version name `1.0.0`, version
  code `1`**. The currently deployed application/recovery-smoke release is
  `0.1.0`; do not treat the intended `1.0.0` value as already deployed.
- The production Play Integrity project number was already present in ignored
  local Terraform configuration. For the future policy rollout, verify the
  ignored release configuration sets the required app version to `1.0.0` before
  generating its final plan; do not change the live `0.1.0` host merely by
  editing Terraform variables.

## Completed technical work

### Release hardening

- Updated the backend dependency pin from `cryptography==49.0.0` to
  `50.0.0` to address the identified high-severity finding.
- Hardened the production Docker image: pinned the Python Alpine base digest,
  pinned build pip, and removed `pip`, `setuptools`, and `ensurepip` from the
  final runtime layer.
- Added a Docker-native build-context guard. It excludes root and nested local
  runtime-secret, Docker/GCloud credential, private-key, and signing paths
  before `COPY . .`, while proving that normal nested Python and JSON inputs
  remain available to the image build.
- Added CI assertions that those build-only modules are absent; CI builds and
  exercises an ARM64 image and gates Trivy on all OS and library HIGH/CRITICAL
  findings.
- Built and scanned the hardened image locally before push; the exact Trivy
  gate had zero HIGH/CRITICAL findings.  Hosted CI independently passed the
  ARM64 runtime and scan gate.
- Implemented the broader pre-canary security, deployment, Android, and
  documentation changes included in commit `c938d7c`.

### Verification completed

- Backend local checks passed: Ruff, Django system check, migration-drift
  check, PostgreSQL 16 migrations through `signage.0011`, and the full
  PostgreSQL-backed suite (210 passed, 2 skipped).
- Dependency audit found no vulnerability for `cryptography 50.0.0`.
- Android production configuration was compiled and signed in a disposable
  test-key environment using the real production API/Play Integrity settings
  and `1.0.0` / `1`; APK Signature Scheme v2 verification passed.  This was
  **not** a production-signed APK.
- `terraform fmt -check -recursive` and `terraform validate` passed. No final
  production plan has been generated from the final immutable image digest and
  current ignored inputs; the fresh plan described below remains mandatory
  before an apply.

### AWS baseline verified (read-only)

- Authenticated AWS SSO identity is a production administrator in account
  `173454940059`, region `ap-southeast-5`.
- The production EC2 instance is healthy and SSM-online; public health
  endpoints returned HTTP 200 at the last check.
- Latest DLM encrypted data-volume snapshot and logical backup/media sidecar
  were both under 24 hours old at the latest 2026-08-09 read-only preflight.
- The project budget was USD 1.916 of the USD 30 target; root MFA is enabled
  and root access keys are absent.

### Recovery rehearsal and final evidence gate

- The 2026-08-01 isolated rehearsal successfully restored the DB snapshot,
  logical backup, and private media within the intended recovery objective.
  Temporary rehearsal resources were removed; estimated extra AWS cost was
  below USD 0.01. It did **not** start the restored Django/Caddy application
  or test an owner login and representative report.
- The later recovery-only fixes listed above harden the isolated path for the
  XFS journal, bootstrap tooling, media proof, Caddy capability and loopback
  exposure, and logical-restore credentials. They do not deploy or alter the
  production application.
- **Automated final drill completed — owner-operated smoke still pending.**
  The reviewed `main` commit was
  `3b9ba958df8a7295962a96fa16a5595d0c56ced7`; operation
  `a4d730a97626bdb3bb652c4e72409d45` used the isolated recovery root and the
  production-equivalent `0.1.0` image
  `duducar-signage-backend@sha256:a9103d0ed09417b62af2b3c6fb0644a6ebe6e164e180a4c37288b255e94dd2fc`.
  It used only snapshot `snap-0e000e16f572c5ab0` from production data volume
  `vol-05b6edc95de87cc4a`, archive
  `database-backups/duducar-signage-postgres-20260808T180321Z.dump` version
  `m34z57aR03cFEWC_MXTpDfq6AoYS_iiF`, matching sidecar version
  `waI9Vf.L_OlHtHYpFvissgCUqtu4UzBK`, and normalized media
  `validated/181f50c7-9074-4550-aa49-02d7bc21f965.png` version
  `XNDO6IO05w6O3gHQhmUQVD.kXdJOQrsj`. At preflight, the DLM snapshot and
  logical backup were respectively about 13 hours 34 minutes and 14 hours 12
  minutes old, within the 24-hour RPO.
  - The clone took the expected guarded XFS dirty-journal path: read-only
    inspection returned the documented exit `3`, explicit clone-only journal
    replay passed its clean post-check, and the clone was then mounted only on
    the disposable host.
  - Snapshot schema/migrations, exact media key/SHA-256/byte-size proof,
    archive sidecar/catalogue validation, logical restore, grant repair, and
    migration checks all passed. The root-only logical reader used a short-lived
    copied credential; the original `10001:10001` credential remained mode
    `0500`/`0400` and the temporary copy was gone afterward.
  - Recovery Caddy/Django passed over the recovery CA only: `/login/` returned
    `200`, an unauthenticated protected request returned `302`, and the sole
    port `8443` listener was `127.0.0.1` via the socket proxy. Caddy had only
    `NET_BIND_SERVICE`, no Docker-published port, and the Docker bridge was
    internal; Django retained `--cap-drop ALL`.
  - The instance launched at `2026-08-09T08:18:50Z` and was terminated at
    `2026-08-09T08:31:53Z` (13 minutes 03 seconds). This demonstrates the
    automated path is within the 24-hour objective, but does **not** establish
    full RTO acceptance because the owner-operated check below was not run.
    Exact billed cost is not yet available; the only temporary usage was this
    short-lived `t4g.small`, its encrypted 16-GiB root and 32-GiB clone, and an
    ephemeral public IPv4.
  - `cleanup-check` and an independent read-only audit both passed. The
    operation's instance, clone and root volumes, ENI, security group, IAM
    role/profile, and EIP association are absent; only AWS terminated-resource
    history remains. The source snapshot remains complete/encrypted and the
    source volume remains attached only to production.

  The remaining hard pre-go-live recovery action is a named account owner using
  the isolated SSM-tunnelled environment to sign in, open the protected
  dashboard, and generate a representative CSV. Do not retrieve, reset, or
  record an owner password to automate that action. No production deployment,
  hardware qualification, production-signed APK, device enrollment/canary, or
  owner-login proof is established by this drill.

## Important unresolved items and risks

1. **No final release image has been pushed to production ECR.** The existing
   `sha256:973fd2…` ECR candidate tagged from `f99a941` predates the current
   release source and must not be reused by inference. Build and scan a fresh
   ARM64 image from the `main` commit that includes
   [#38](https://github.com/czeyik/Digital-Signage-Project/pull/38) immediately
   before the authorized rollout, record its immutable digest, set
   `container_image` to that digest in the ignored production
   tfvars, then regenerate the plan. The host and isolated worker must use that
   same digest, paired with `required_app_version = "1.0.0"`. Never deploy a
   mutable tag.

2. **Terraform has not been applied.** A fresh plan, generated only after the
   final digest and ignored inputs are reviewed, is authoritative. Based on the
   latest read-only production assessment, it should account for **3 additions,
   2 changes, and 1 worker-revision replacement/removal**:

   - create the SSM runtime-assets document and the SSM release-config
     document;
   - create the new isolated Fargate worker task-definition revision;
   - add `ecs:ListTasks` to the EC2 media-dispatch role and reference the new
     worker task definition;
   - change S3 backup noncurrent-version retention from 30 days to the
     user-approved 1 day; and
   - replace the Terraform state reference currently at worker revision `:1`
     with the new revision.

   Do not call the retired worker revision physically destroyed without plan
   evidence: the source uses `skip_destroy = true`. The read-only assessment
   found no intended changes to the EC2 instance, volumes, EIP, DNS,
   CloudFront, RDS, ALB, ECS service, or a running task, but the fresh plan is
   the final authority. The 1-day retention change is a deliberate
   deletion/retention decision once applied.

3. **IAM must be applied before the host image deployment.**  New media
   dispatch logic requires `ecs:ListTasks`; current live EC2 permissions lack
   it.  Deploying the web image first can break media dispatch.

4. **Migrations `0009` through `0011` have not been run in production.**
   `0009_revoke_marketing_admin_access` clears dashboard sessions, removes
   `is_staff`/`is_superuser` from every marketing-role account, and appends an
   audit event. `0010_battery_backed_player_policy` invalidates every approved
   `HardwareQualification`, adds false-default battery-backed gates, makes the
   historic heartbeat power fields nullable, and extends operational-event
   choices. Before it runs, record affected active devices and qualifications,
   keep affected devices in maintenance until requalified, and preserve/triage
   retired power-policy alerts rather than deleting them. In `0011`, the
   physical-column repair acts only for a database that received the superseded
   rename and is a no-op on the expected legacy physical-column layout; the
   migration itself must still run to invalidate incomplete re-approvals and
   add the database constraint requiring all 19 current qualification fields
   for approval. It fails closed on an unexpected column layout; do not
   manually rename columns if it fails. The legacy-column bridge is for
   isolated, read-only historic investigation only: it does not restore the
   retired vehicle-power criteria, authorize a pre-policy image, or make an
   old image a supported live rollback. Repeat the owner-survival check
   immediately before migration, communicate the forced logout, and have the
   owner re-authenticate afterward. Do not run `create_initial_owner` on this
   live database; it is bootstrap-only and will fail once a user exists.

5. **Owner-operated recovery smoke is still required.** The automated clone,
   snapshot/logical restore, media, TLS, unauthenticated protected-route, and
   cleanup checks are recorded above. A named owner must still sign in through
   the isolated SSM tunnel, open the authenticated dashboard, and produce a
   representative CSV without exposing credentials. Record that evidence in
   `docs/backup-restore.md`; do not keep a recovery host running solely to wait
   for the owner.

6. **Physical hardware qualification is incomplete.** Exact 10-inch pilot
   tablet model and firmware must have an evidence-backed
   `HardwareQualification` record with all 19 current battery-backed playback,
   battery telemetry/runtime, planned shutdown and visible **Resume DUDU**
   recovery, physical shutdown recovery, abnormal-exit, kiosk, screen-state,
   heat, mounting, and evidence gates passed. Legacy boot-on-vehicle-power and
   external-power-loss results cannot approve hardware. Emulator/phone checks
   and a disposable signing-key APK are not release acceptance.

7. **Real production signing is still pending.** Produce the protected-key
   Android APK at version name `1.0.0`, version code `1`, record its signer,
   checksum, certificate, and rollback artifact, and ensure its version name
   matches the server requirement. Use the established signing process and
   secure secret store; do not place keystore files, passwords, private keys,
   enrollment secrets, or production tfvars in Git or chat.

8. **Operational notifications are not proven.**  The SNS subscription exists
   but delivery has not been tested.  Include an authorized, non-sensitive
   alert-delivery test in the maintenance-window validation.

9. **Manual bootstrap snapshot needs an owner decision.**  Snapshot
   `snap-0da33c455687b6128` remains beyond its `ReviewAfter` date.  Retain or
   delete it only after explicit approval; do not delete it automatically.

## Recommended continuation order

The sequence matters; it avoids a partial deployment that loses media-dispatch
capability.

1. **Synchronize the local checkout deliberately.**  Check `git status`,
   `git fetch origin`, inspect the merge on `main`, and make any necessary
   handoff/document commit separately from the release commit.
2. **Complete and record the owner-operated recovery smoke.** Authorize a new
   temporary isolated environment only when the named owner is available, then
   use production-equivalent secrets only there to test owner login and one
   representative report. Clean it up and append the evidence; do not reuse a
   prior operation ID or recovery state key.
3. **Complete real-device qualification.**  Record the exact model, firmware,
   evidence, and all required pass results before treating the Android build as
   releasable.
4. **Produce the immutable release image.** Build and scan a fresh ARM64
   backend image from the reviewed current source, then—only with explicit
   rollout authority—push it to production ECR, capture the pushed digest, and
   set ignored production `container_image` to it. Confirm
   `required_app_version = "1.0.0"` in the ignored production tfvars. Do not
   reuse the historical `f99a941` candidate.
5. **Run a fresh Terraform plan and obtain explicit apply authority.** It
   should account for both SSM documents, the worker revision, EC2 IAM
   permission, and the approved S3 lifecycle update. Review every action,
   including the one-day noncurrent-version deletion implication and the
   worker-revision `skip_destroy` behavior.
6. **Apply Terraform first.** Confirm both SSM documents exist and the EC2
   role now permits `ecs:ListTasks` before deploying the host image.
7. **Schedule a maintenance window and obtain explicit rollout authority.**
   Immediately beforehand: verify an active owner again, confirm recent
   backups, communicate the forced dashboard logout, and ensure a rollback
   operator is available.  Do not assume source merge authorizes this step.
8. **Deploy the host image and runtime assets through SSM.**  Use the pinned
   ECR digest, execute migrations once, then verify application health, owner
   re-login, dashboard access, media upload/processing, and dispatch
   authorization.
9. **Run a one-device physical canary.**  Create the real production-signed
   `1.0.0`/`1` APK, enroll one qualified device, validate kiosk behavior,
   media download/playlist activation, proof-of-play, heartbeat, app-version
   reporting, and alert delivery.  Record outcome before expanding to the
   remaining devices.
10. **Only after successful canary, authorize expansion.**  Keep the canary
    evidence, monitor cost/backup status, and retain an explicit rollback
    decision for any failure.

## Approved battery-backed Android player policy — merged to `main`, not deployed

This user-approved change is merged through [#26](https://github.com/czeyik/Digital-Signage-Project/pull/26),
[#27](https://github.com/czeyik/Digital-Signage-Project/pull/27), and
[#28](https://github.com/czeyik/Digital-Signage-Project/pull/28). It remains
pending exact-hardware qualification, release signing, and authorized
deployment. **No production database migration, image or APK deployment,
device enrollment, hardware canary, or owner-login proof has occurred for this
policy.**

### Locked product decisions

- Support only battery-backed Android tablets; remove the battery-free path.
- Advertising plays while the tablet itself has power, including battery power.
  It no longer depends on vehicle/external power detection.
- Every completed advertisement counts as a completed play, including
  battery-powered or parked playback.  Reports must not imply that the vehicle
  was operating, occupied, or externally powered.
- New heartbeats do not collect external-power or charging telemetry.  Battery
  percentage, temperature when available, screen state, storage, app/Android
  version, sync state, and playback state remain health telemetry.
- At battery `<=20%`, create a warning; at `<=10%`, escalate it to critical.
  Neither threshold stops advertising.
- Any physical user may use a normal visible **Prepare for shutdown** button;
  it is deliberately not PIN-protected.  This supersedes the old rule that a
  driver has no in-app pause/stop control.
- After confirmation, the player interrupts the current item, shows a neutral
  shutdown-ready screen, and stays stopped until someone confirms the visible
  non-PIN **Resume DUDU** action. Launcher/activity/lifecycle restoration alone
  does not resume advertising. There is no five-minute automatic resume.
- The user then uses the exact tablet's documented physical power-off method.
  After full battery depletion/reboot, recovery is best effort and staff must
  unlock and launch DUDU to reach the shutdown-ready screen, then confirm
  **Resume DUDU**.

### Replacement health policy

Retire vehicle-power-loss semantics.  Do not call an interruption a confirmed
power loss or driver misconduct.

- **Low battery:** a device heartbeat at `<=20%` opens/escalates a
  `low_battery` warning; at `<=10%` it escalates that same unresolved alert to
  critical.  Alerts remain open until an authorized dashboard user acknowledges
  them.
- **Abnormal application stability:** on Android 13, after a successful
  manifest sync anchors server time (on that sync or a later one), inspect
  `ActivityManager.getHistoricalProcessExitReasons()` and record idempotent
  diagnostic operational events for app crash, native crash, ANR,
  initialization failure, supported low-memory kill, excessive-resource use,
  and freezer termination. A warning `repeated_abnormal_app_exit` opens after
  three distinct abnormal events received in a rolling 24 hours.
- **Device availability:** use server-received heartbeat time, not client time
  or an individual ad duration.  At 24 hours without a heartbeat, open a
  `device_unavailable` warning; at 48 hours, escalate the same unresolved alert
  to critical.  This replaces the invalid old “one interruption over 24 hours”
  check.
- A planned local shutdown marker may suppress abnormal-exit classification
  after an orderly recovery for up to 24 hours.  It must not suppress the
  server-side 24-hour unavailable alert, because the preparation event may not
  reach the server before the tablet is offline.

`ApplicationExitInfo` is diagnostic and uses a finite Android-maintained
history, so it supplements heartbeat liveness rather than proving every device
shutdown.  See the official [ActivityManager API](https://developer.android.com/reference/android/app/ActivityManager)
and [ApplicationExitInfo API](https://developer.android.com/reference/android/app/ApplicationExitInfo).

### Merged scope and remaining validation

The following policy changes are merged to `main`. Backend checks (Ruff, Django
checks, migration drift, development readiness, and `209 passed, 2 skipped`)
and Android Docker checks (16 JVM tests plus debug and AndroidTest Kotlin
compilation) pass. Exact-hardware qualification, release review/signing, and
authorized deployment remain required.

1. **Android player**

   - External-power gates are removed from sync, cached playback, playback
     start, fallback recovery, and `FLAG_KEEP_SCREEN_ON`; the screen stays
     awake only while visible media playback is active.
   - `ACTION_POWER_CONNECTED`/`ACTION_POWER_DISCONNECTED` behavior and the old
     boot-reconnect dependency are removed; an unplugged tablet continues the
     current playlist normally.
   - The visible confirmed shutdown preparation flow records a
     `planned_shutdown` interruption, persists a local preparation marker, and
     shows neutral physical-shutdown instructions until the visible non-PIN
     **Resume DUDU** confirmation. A launcher/activity/lifecycle restoration is
     not a deliberate restart. The player dynamically observes an orderly
     Android shutdown only to classify later recovery and never performs
     network work during shutdown.
   - After a successful manifest sync anchors server time (on that sync or a
     later one), the player queries and deduplicates Android exit history with
     a local cursor, enqueues non-sensitive operational diagnostics, and
     continues existing checkpoint/proof recovery without duplicate evidence.

2. **Backend and data contract**

   - `DeviceHeartbeat.external_power` and `charging` are nullable through a
     backwards-compatible Django migration. New APKs do not send them, while
     historic rows remain through normal operational-data retention.
   - The existing operational-event endpoint/model choices are extended for
     planned shutdown and abnormal app-exit diagnostics with strict validation
     and idempotency; no new public endpoint is introduced.
   - The new `planned_shutdown` playback interruption reason is accepted, while
     old external-power interruption reasons remain accepted for old clients and
     immutable historical evidence.
   - `evaluate_device_health` uses battery, abnormal-exit, and heartbeat-
     liveness logic with an open-or-escalate helper so `low_battery` and
     `device_unavailable` change severity rather than opening duplicate
     unresolved alerts.

3. **Requirements and operations documentation**

   - `AGENTS.md`, `docs/hardware-qualification.md`, `docs/device-api.md`,
     `docs/production-deployment-runbook.md`, and this handoff are updated to
     remove vehicle-power playback gating, automatic power-reconnect claims,
     the battery-free path, external-power/charging heartbeat fields, and
     vehicle-disconnect-as-stop-control language.
   - The documentation states that all completed battery/parked plays count,
     while reports are not evidence of vehicle operation or audience exposure.
   - The old driver restriction is replaced with the intentionally visible
     shutdown preparation control and a documented physical power-off/restart
     procedure.
   - Hardware-qualification fields/tests with obsolete boot-on-power and
     external-power-loss names are redefined with auditable new criteria rather
     than silently reusing old pass flags.

4. **Testing and canary gates**

   - Android JVM/instrumentation coverage includes battery-powered playback,
     screen-awake policy, planned-marker/Resume durability, orderly-shutdown
     suppression, pre-anchor event rebasing, exit-history deduplication, and
     proof recovery. The visible `MainActivity` confirmation flow still needs
     an emulator and exact-device run.
   - Backend coverage includes legacy heartbeat compatibility, nullable
     telemetry, 20%/10% threshold boundaries, three abnormal exits in 24
     hours, 24-to-48 hour alert escalation, planned-shutdown exclusion, and
     immutable/idempotent evidence.
   - On the exact Android 13 model/firmware, qualify battery runtime, 20%/10%
     alert delivery, thermal/direct-sun behavior, physical power-off, full
     depletion, staff unlock/launch to the shutdown-ready screen and **Resume
     DUDU** confirmation, reboot, sleep/Doze/OEM process kill, remote
     disablement, and proof-upload recovery. Production enrollment remains
     blocked until its `HardwareQualification` record is approved and Play
     Integrity succeeds.

### Rollout order and residual risks

1. Deploy the backwards-compatible backend migration/API first; existing APKs
   must continue to function during the transition.
2. Deploy the signed Android release containing the new policy to one qualified
   physical canary only.
3. Verify the revised alerts, shutdown flow, recovery, and reporting before
   enrolling more devices.

Continuous battery-powered display/video can drain the battery quickly and may
leave the tablet offline until staff recovers it.  It can also run ads in a
parked or unattended vehicle, which the user has explicitly chosen to count.
Monitor proof/heartbeat volume, cellular use, device temperature, and the USD
30 target during the canary.  `FLAG_KEEP_SCREEN_ON` is an Activity-only display
request, not a guarantee against OEM process killing or post-depletion
recovery; Android warns that it can drain battery quickly.  See [Android screen-on guidance](https://developer.android.com/develop/background-work/background-tasks/awake/screen-on).

## Key files to read before acting

- `AGENTS.md` — product, security, production-topology, and verification
  constraints.
- `docs/production-deployment-runbook.md` — rollout order, owner preflight, forced logout,
  and deployment actions.
- `docs/backup-restore.md` — recovery evidence and approved retention change.
- `docs/android-release-signing.md` — first-production release identity and
  secure signing procedure.
- `docs/architecture.md` — target topology and dispatch status wording.
- `infrastructure/README.md` and `infrastructure/terraform/` — bootstrap-only
  owner warning, Terraform parameters, and production plan workflow.
- `.github/workflows/ci.yml` and `backend/Dockerfile` — ARM64/runtime/Trivy
  security gates.

## Safety reminders for the next agent

- Do not apply Terraform, push a production image, migrate production, invoke
  SSM deployment commands, merge further code, or delete snapshots without
  explicit user authority for that action.
- Keep ignored production files ignored: `terraform.tfvars`, signing material,
  secrets, backup archives, and generated APKs must never be committed.
- The existing production state is healthy, but it is a single-host pilot
  architecture; prefer tested, reversible, maintenance-window changes over
  broad refactors or topology changes.
