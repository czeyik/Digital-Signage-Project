# DUDU Car — Continuation Handoff

**Updated:** 2026-08-18, Asia/Kuala_Lumpur

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

- `origin/main` and the reviewed clean release source are
  `44dac051b73919737124f98399e56331069bc52f` at
  `/tmp/duducar-owner-smoke-main-44dac05`.
- Do **not** build or deploy from this primary workspace: it is
  `fix/recovery-xfs-journal-replay` at `ab073e8` and has uncommitted
  documentation changes.
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
- Production APK signing is complete: `com.duducar.signage` 1.0.0 / code 1,
  signed with the new RSA-4096 company key. APK, R8 mapping, checksums, and
  non-secret evidence are in the encrypted company vault; two independent
  encrypted key backups are confirmed. Never commit, print, or copy the key.
- ARM64 backend image was built/scanned and pushed with no HIGH/CRITICAL ECR
  findings: `173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:b9a3dc42c985fd28485bc0cc679e8f6c19706e70c14927485728e0caab60d2be`.

## Production preparation status (last verified 2026-08-10)

> **Source-only hardening update (2026-08-18):** the current uncommitted
> workspace replaces the prior two-file runtime document and manual deployment
> sequence with a complete runtime-bundle document, complete release-config
> document, and a separate operation-confirmed activation document. None of
> these changes has been planned, applied, or run in AWS. The previously recorded
> `08cc...` install operation targets an obsolete document/config shape and must
> not be resumed. Start a new clean reviewed commit, plan, document versions/
> hashes, and operation ID. Before activation, add exact digest-pinned
> `postgres_image` and `caddy_image` Terraform values and add
> `WORKER_DB_PASSWORD` plus canonical
> `PLAY_INTEGRITY_APP_CERTIFICATE_SHA256` to the application secret. The sole
> supported activation path is `production_release_activation_document`; it
> owns deploy, migration `0012`, runtime grants, readiness, digest/version/unit
> assertions, and the post-release verified backup. Do not run those commands
> manually.

- Terraform applied only the reviewed release-support scope: SSM release
  documents, runtime assets, IAM/worker revision, and approved backup
  noncurrent-retention change. A fresh post-apply plan was empty.
- The superseded two-file runtime assets were validated and installed at that
  date. No host stack, application image, migration, or canary was deployed.
- Release-config validation operation `08ccbebf3c7672087b96875e76884479`
  belongs to the superseded document and must never be installed or resumed;
  follow the source-only hardening note above with a fresh operation.
- Migrations `0009`–`0012`, notification end-to-end test, production rehearsal,
  and vehicle enrollment remain pending. Re-run read-only AWS/owner preflight
  before any production action; do not treat this dated status as live proof.

## Blocking gates

1. Obtain an exact Android 12+ tablet or OEM-signed Android 12+ firmware with
   documented support and current verified patch level.
2. Complete full exact-model/firmware hardware qualification, including
   device-owner/lock-task, battery, LTE, media, shutdown/recovery, thermal,
   kiosk escape, and factory-reset revocation evidence.
3. Require a real `MEETS_DEVICE_INTEGRITY` result before production enrollment.
4. Complete the remaining runbook rehearsal, controlled SNS/EventBridge
   notification delivery test, and owner/migration preflight.

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

Wait for an Android 12+ candidate. Before installing anything, run a read-only
ADB preflight on the exact tablet and record model, firmware, Android API
(`ro.build.version.sdk >= 31`), platform/vendor patch, verified-boot state, and
Google Play availability. If it passes, begin the documented hardware
qualification in an isolated qualification/development workflow; do not issue
production enrollment until its `HardwareQualification` record is approved.
