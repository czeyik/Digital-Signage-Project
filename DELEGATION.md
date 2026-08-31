# Production Pilot Delegation

**Last updated:** 2026-08-31 (Asia/Kuala_Lumpur)

**Owner and authority for every role:** Cze Yik

This is the current execution handoff for remediating the Wave 9 findings and
launching the DUDU pilot. Resolved setup history has been removed. Secure
evidence remains in its recorded vault locations and is not duplicated here.

## Execution rules

1. Execute one listed wave at a time in numeric order. Do not start a wave until
   every prerequisite is `Complete`.
2. Start each wave by reading `AGENTS.md`, this file, and only the sources named
   by that wave. Revalidate repository and live facts; historical evidence is
   not standing authorization.
3. Ask Cze Yik for missing intake as one concise bundle. Use approved vaults or
   authenticated local sessions and never place secrets, PINs, driver PII,
   tokens, signing keys, or recovery codes in chat, Git, logs, or this file.
4. After intake, create one Codex Goal using the wave's exact **Goal**. Do not
   set a token budget unless Cze Yik supplies one.
5. Keep the change surface limited to the wave. Before completion, record the
   evidence location, update the gate map and current-state summary, append one
   concise completion-log entry, and complete the goal.
6. A later defect that invalidates an earlier gate blocks the current wave and
   reopens the earliest affected wave. Replay its dependent gates in order.

Allowed statuses are `Waiting`, `Pending`, `In progress`, `Blocked`,
`Reopened`, and `Complete`. Only one wave may be `In progress`.

## Fixed decisions

- Pilot scope is at most 10 vehicles, in Malaysia, with GPS and self-hosted
  OpenMapTiles. The accepted infrastructure limits remain USD 30/month,
  24-hour RPO/RTO, and one production-host failure domain.
- The replacement Android build must have a version code higher than the
  installed code 4 build; therefore the next production build is code **5 or
  higher**.
- The first installation of the replacement APK is manual. All OTA release
  fields remain disabled unless Cze Yik authorizes a separate OTA release.
- A scheduled playlist must synchronize at its server-defined boundary and
  replace the prior playlist only after complete atomic download and validation.
- Supported production media are JPG/JPEG, PNG, and MP4. The stored delivery
  object, not merely its processing precursor, must satisfy the declared hash,
  size, type, decode, and player-dimension limits before publication.
- **Disable playback** revokes playback credentials only. The device-specific
  management credential survives so the dashboard can request Admin mode from
  enrollment. The explicit **Revoke credentials** action revokes both channels.
- Admin mode is entered remotely from the dashboard Devices page and exposes
  only **Exit DUDU** and **Prepare for shutdown**. Normal playback, fallback,
  maintenance, and enrollment expose no local admin gesture, PIN flow, exit, or
  shutdown action.
- Canary acceptance is **one consecutive hour** of smooth production playback.
  An interruption restarts the hour after remediation. This does not change
  the 24-hour backup RPO/RTO or recovery-freshness gates. Do not invent
  additional quantitative canary thresholds.
- `approved_for_pilot` and fleet expansion are separate, explicit Cze Yik
  decisions. Tests and operators must not infer either decision.
- Wave 4 and its pre-production physical qualification gate are removed by
  final owner decision. The first physical proof of the replacement APK and the
  four remediated behaviors occurs in the Wave 9 production rehearsal.
- Any expired production or canary window must be replaced before a live
  mutation. Old plans, SSM commands, recovery operations, approvals, and health
  results are audit history and must never be resumed.

## Planning baseline — revalidate before relying on it

- The remediated server candidate is committed on local `main` at `43845d5`.
  It includes the reviewed `e8d2559` remediation, canonical rule reconciliation,
  and populated migration rehearsal through `0016`; it has not been pushed.
- Wave 1's stopped-state recovery repair remains accepted at `16cc6fe`; it is
  not affected by the current remediation and does not need replay.
- The last recorded production/canary release was `8087380`, Android code 4,
  with server migrations through `0015`. One Lenovo `HA259E36` ran that canary.
  These are historical records; current live state must be read again before
  use.
- The Wave 9 report says playback was disabled and re-enrollment then failed.
  Do not assume the tablet's current status or credential state; read both from
  production and the physical device at the owning wave.
- The local worktree contains the minimum remediation for all four observations:
  exact scheduled sync, stored JPG/JPEG/PNG validation, reusable enrollment UI,
  and remote Admin mode backed by a management credential that survives
  playback disablement. Django migration `0016_device_management` is included.
- Local verification currently records 278 backend tests passed, 2 skipped;
  Django migration/system checks and Ruff passed; Android development unit
  tests, instrumentation compilation, and lint passed.
- The replacement 4.1.0/code-5 APK is signed and retained under
  `wave-3-remediation-20260831`; production has not been upgraded and no new
  canary has started.
- Existing code 4 devices do not have a management credential until the new app
  enrolls or bootstraps one while its playback credential is still valid. A
  never-enrolled installation has no dashboard device association and cannot be
  remotely targeted.
- Backend must be deployed before the replacement APK. The upgraded app must
  bootstrap its management credential before playback-disable testing.
- No representative non-production database copy is currently recorded. Never
  use production data locally; if none is supplied, Wave 2 may use an isolated
  synthetic dataset that covers populated legacy migrations.

The canonical production authority remains
`docs/production-deployment-runbook.md`. If another document conflicts with
the decisions above, reconcile it in the wave that owns the affected source.

## Production gate map

| Wave | Gate | Prerequisite | Status | Current handoff |
|---|---|---|---|---|
| 1 | Stopped-state recovery repair | None | Complete | `16cc6fe`; retained runtime and recovery evidence |
| 2 | Remediated server release candidate | Wave 1 | Complete | `43845d5`; 278 passed, 2 skipped; populated `0015` → `0016` rehearsal; migration/system/Ruff checks |
| 3 | Signed replacement Android artifact | Wave 2 | Complete | `wave-3-remediation-20260831`; 4.1.0/code 5; source `43845d5`; checksum/signature/certificate/build checks passed |
| 5 | Immutable deployment package and plan | Wave 3 | Waiting | Rebind only changed release inputs; reuse verified unchanged assets |
| 6 | Current launch authorization packet | Wave 5 | Waiting | Refresh expired approvals/window and bind the replacement release |
| 7 | Production upgrade | Wave 6 | Waiting | Deploy backend/migration first; do not enroll or disable a tablet |
| 8 | Current isolated recovery proof | Wave 7 | Waiting | Prove the upgraded schema and exact release recover within 24 hours |
| 9 | Focused rehearsal and one-device canary start | Wave 8 | Waiting | Prove all four fixes in production, then start the one-hour clock |
| 10 | Canary acceptance and controlled fleet expansion | Wave 9 | Blocked | No expansion until the new one-hour canary passes |

## Wave 2 — Freeze the remediated server candidate

**Goal:** Produce a clean, tested server release candidate containing the Wave
9 remediation, safe migration `0016`, and compatible behavior for the installed
code 4 player and replacement code 5+ player.

**Read:** `OVERVIEW.md`, `docs/architecture.md`, `docs/device-api.md`,
`docs/openmaptiles.md`, `docs/deployment-readiness.md`, and the affected backend
models, API, services, migrations, and tests.

**Intake:** Confirm the authoritative installed-app inventory, whether a
sanitized/restored non-production database is available, and the release
branch/commit destination. No code 2/3 identity is expected to be retained;
record any contrary current fact before proceeding.

**Work:** Review the existing remediation rather than rewriting it. Verify
management/playback credential separation, enrollment compatibility, scheduled
transition timestamps, stored-image delivery validation, authorization/privacy,
GPS/location retention and health, authenticated map routes, and old-client
handling. Rehearse all migrations through `0016` on the representative or
approved synthetic dataset. Run the full backend, migration, system, lint, and
readiness-relevant checks. Reconcile the one-hour canary wording in canonical
documentation. Do not build Android, sign artifacts, or mutate AWS.

**Complete when:** migration and compatibility evidence passes, the full server
suite is green, the reviewed worktree contains no unrelated changes, and one
clean release commit is recorded.

## Wave 3 — Build and sign the replacement Android release

**Goal:** Produce and securely retain one production-signed code 5+ DUDU APK
from the exact Wave 2 commit, with a reproducible manifest and certificate
continuity.

**Read:** `docs/android-build-verification.md`,
`docs/android-release-signing.md`, `docs/device-api.md`, and `android-player/`.

**Intake:** Obtain the exact version name/code, Wave 2 commit, approved build
environment, signing-vault access and backup confirmation, Play Integrity
project/decode access, and secure artifact/evidence destinations.

**Work:** Build from the clean pinned commit. Run unit tests, instrumentation
compilation/execution where available, lint, development and production compile,
R8/signature checks, and reproducibility checks. Verify package identity,
minimum API, higher version code, signing certificate, SHA-256, size, mapping,
GPS permissions, Keystore credential storage, disabled OTA configuration, and
the management credential's survival of playback-credential clearing. Do not
install on production hardware or touch AWS.

**Complete when:** the signed APK, mapping, checksums, certificate verification,
and reproducible manifest are independently verifiable in the approved vault.

## Wave 5 — Prepare the immutable upgrade package

**Goal:** Produce a pinned, scanned, costed upgrade package and fresh reviewed
deployment plan for the remediated backend and code 5+ APK without applying it.

**Read:** `infrastructure/README.md`, `docs/openmaptiles.md`,
`docs/aws-cost-estimate.md`, `docs/production-deployment-runbook.md`, and the
release/Terraform definitions.

**Intake:** Obtain AWS SSO and protected configuration access, ECR and artifact
store access, the Wave 2 commit, Wave 3 manifest, current production read-only
state, secure evidence destination, and authorization/cost limit for builds,
scans, uploads, refresh, and planning.

**Work:** Build and scan only release components affected by the pinned source;
reverify and reuse unchanged PostgreSQL, Caddy, MBTiles, and infrastructure
artifacts when their identity remains valid. Bind backend digest, code 5+ APK,
migration `0016`, required app version, runtime/release document versions, and
empty/zero OTA fields into one manifest. Refresh state and create the smallest
fresh plan; reject unexpected topology, public access, destructive changes, or
cost above the accepted limit. Do not apply or activate.

**Complete when:** the immutable manifest and exact unapplied plan are
checksum-bound, reviewed, current, non-destructive, and within budget.

## Wave 6 — Refresh launch authorization

**Goal:** Bind current human, account, content, support, rollback, cost, privacy,
and maintenance-window authorization to the replacement release.

**Read:** the runbook's pre-change and rehearsal sections,
`docs/aws-cost-estimate.md`, and the Wave 3 and Wave 5 handoffs.

**Intake:** Obtain current account/contact status, owner and marketing access,
approved remediation-test media and rights, secure driver/vehicle assignment,
support availability, rollback authority, and a new production/canary window.

**Work:** Revalidate existing account, DNS/TLS, SMTP/SNS, budget, privacy,
content, roster, and support evidence. Repeat an external exercise only when its
fact expired, changed, or is required by the replacement release. Prepare a
focused UAT script for the four remediated behaviors and record Cze Yik's
distinct release, cost, privacy, rollback, and change-window decisions. Do not
deploy or enroll.

**Complete when:** the current launch packet binds the exact Wave 5 package,
all changed or expired prerequisites are green, and the production window and
rollback authority remain valid.

## Wave 7 — Upgrade production

**Goal:** Upgrade the live production stack to the exact remediated release,
apply migration `0016`, and leave it ready, backed up, and OTA-disabled without
enrolling or disabling a tablet.

**Read:** the complete production runbook, `infrastructure/README.md`,
`docs/deployment-readiness.md`, `docs/backup-restore.md`, and the Wave 5/6
handoffs.

**Intake:** Obtain the valid maintenance window, AWS SSO/vault access, exact
plan/manifest, current approvals, secure evidence destination, and availability
for just-in-time apply/rollback confirmations.

**Work:** Recheck live state, backup/snapshot freshness, drift, secrets schema,
plan freshness, and rollback inputs. Take the required pre-change backup. Apply
only the reviewed plan and deploy through the supported SSM path with fresh
operation/command IDs. Deploy backend support before the APK is installed;
apply migrations through `0016`; verify image digest, required app version,
readiness, public routes, authenticated maps, timers, backup receipt, snapshot,
and disabled OTA fields. Do not reuse historical commands or operations.

**Complete when:** production runs the exact Wave 5 backend with migration
`0016`, all readiness/recovery prerequisites are current, and no tablet has been
enrolled, upgraded, disabled, or used for acceptance.

## Wave 8 — Prove recovery of the upgraded release

**Goal:** Demonstrate isolated recovery of the upgraded schema, exact release,
and media within the accepted 24-hour RPO/RTO, then remove every temporary
resource.

**Read:** `docs/backup-restore.md`, `infrastructure/recovery-smoke/README.md`,
the runbook recovery gate, and Wave 7 backup/snapshot evidence.

**Intake:** Obtain AWS SSO, authorized current logical archive, DLM snapshot,
media versions, expected records, isolated credentials, temporary
region/window/cost approval, evidence destination, and teardown authorization.

**Work:** Use a new recovery operation to restore the logical archive and DLM
clone in isolation with the pinned release. Verify migration `0016` data,
management-command tables, exact media, readiness, owner login, reports, CSV,
and RTO without routing through production or fabricating data. Capture
non-secret evidence and destroy the isolated environment.

**Complete when:** logical, snapshot, schema, and exact-media recovery pass
within 24 hours and independent cleanup proves no temporary billable resource
remains.

## Wave 9 — Run focused rehearsal and start the canary

**Goal:** Pass the focused production rehearsal on exactly one approved code 5+
tablet and establish the timestamped start of the one-hour canary.

**Read:** the runbook's hardware/rehearsal/canary sections,
`docs/deployment-readiness.md` and the Wave 3 and Wave 5–8 handoffs.

**Intake:** Obtain production owner/marketing access, approved UAT media,
selected tablet and APK, secure assignment data, physical/ADB access,
support coverage, external `approved_for_pilot` availability, exact enrollment
window, and evidence destination.

**Work:** Revalidate production DNS/TLS, authorization/privacy, private media,
processing, backups, alerts, budget, maps, and GPS without repeating unrelated
historical failure drills. Manually install the exact APK. If retained playback
credentials are valid, bootstrap management; otherwise reactivate and enroll
the upgraded app so enrollment issues both credentials. Confirm the management
credential before disabling playback. Enroll exactly one device and use a
playlist containing JPG, JPEG, PNG, and MP4. Prove scheduled activation,
playback, disablement, remote Admin mode from every required state, Exit DUDU,
admin-only shutdown, reactivation, and re-enrollment. Confirm telemetry/evidence
and OTA-disabled state. Resolve every defect before recording a new MYT canary
start and baseline.

**Complete when:** exactly one approved device is healthy on the exact release,
all four remediations pass in production, operational gates remain green, and
the uninterrupted one-hour interval has an explicit MYT start timestamp.

## Wave 10 — Accept the canary and expand the fleet

**Goal:** Prove one consecutive hour of smooth one-device production, obtain
Cze Yik's separate expansion approval, and launch owner-approved devices one at a
time without exceeding the 10-vehicle pilot scope.

**Read:** the Wave 9 baseline, current release manifest, production runbook
decision rules, and current dashboard/alert evidence.

**Intake:** Obtain monitoring/log access, checkpoint/support schedule, physical
access to the canary, secure assignments and device inventory for the remaining
pilot devices, and Cze Yik's availability for the post-canary decision.

**Work:** Record availability, heartbeat, scheduled sync, all media types,
proof, GPS/map, alerts, backups, worker health, and cost through the hour. An
interruption restarts the hour; a source, artifact, hardware, or infrastructure
change reopens its owning wave. After the hour, obtain explicit expansion
approval. Install, integrity-check, approve, enroll, and smoke-test each device
one at a time. Keep OTA disabled.

**Complete when:** evidence proves one smooth hour, Cze Yik approves expansion,
every launched device is owner-approved and healthy, fleet count is at most 10, and
the pilot/support handoff is recorded.

## Retained evidence and status log

Historical evidence remains authoritative for what happened, but it does not
clear a current gate unless the relevant wave explicitly revalidates and binds
it to the replacement release.

- Stopped-state recovery repair: commit `16cc6fe`.
- Prior release/activation package:
  `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-5-release-replay-20260829-r2/`.
- Replacement signed Android release:
  `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-3-remediation-20260831/`.
- Prior recovery proof:
  `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-8-recovery-2f2b27eb02698522c37be533d7a7697d/`.
- Defective canary evidence:
  `/home/czeyik/.local/share/Cryptomator/mnt/dspvault/duducar-signing/wave-9-replay-20260830/`.

Current state: Waves 1–3 are `Complete`; Waves 5–9 are `Waiting`; Wave 4 is
removed by final owner decision; Wave 10 is `Blocked`.
Production and the enrolled tablet have not been changed by the local
remediation. No fleet expansion is recorded.

| Timestamp (MYT) | Wave(s) | Status | Summary |
|---|---|---|---|
| 2026-08-29 | 1, 7, 8 | Complete | Recovery repair, production activation, and isolated recovery passed for the prior release. |
| 2026-08-30 05:45:56.939 | 9 | Complete, later invalidated | One code 4 Lenovo began the prior canary; the interval later exposed four acceptance defects. |
| 2026-08-31 13:07 | 2–3, 5–10 | Reopened / Waiting / Blocked | Local source remediation passed backend and Android checks; production, signing, deployment, physical proof, and a new canary remain pending. |
| 2026-08-31 | Framework | Updated | Canary acceptance changed to one consecutive hour; 24-hour recovery objectives remain unchanged. |
| 2026-08-31 | Framework | Redesigned | Resolved chronology was condensed and active Waves 2–3 and 5–10 were refocused on the current remediation and replay. |
| 2026-08-31 | 4 | Removed | Cze Yik made the final decision to remove pre-production physical qualification; Wave 9 now owns the first physical proof. |
| 2026-08-31 | 2 | Complete | `43845d5`; full server suite and populated migration rehearsal passed; no AWS or Android build/signing action occurred. |
| 2026-08-31 | 3 | Complete | `wave-3-remediation-20260831`; signed 4.1.0/code-5 APK from `43845d5` passed build, lint, checksum, payload reproduction, and certificate-continuity checks. |
