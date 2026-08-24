# DUDU Car — Production Release Handoff

**Updated:** 2026-08-24, Asia/Kuala_Lumpur

## Objective

Reach the first one-device Malaysia production canary without weakening the
Android 12+, private-media, SSM-only, backup/recovery, or USD 30/month
controls.

## Latest release-build update (2026-08-24)

- Investigated an Android packaging failure while unlocking the
  `duducar-production` PKCS#12 private key. The project signing configuration
  supports PKCS#12; no source or build-config change was needed.
- A subsequent production release build completed successfully. No signing
  password, key material, or other secret was recorded or changed.
- The signed artifact was moved out of `/tmp` to
  `/home/czeyik/dudu-signage-1.0.0-9d71fe1.apk` (do not commit it).
- SHA-256:
  `4424e4d26cc9a1bdcb15cc8630f589ee513658059cdc91456dde4c55e3707c57`.
- Before activation, verify the APK certificate/version against the release
  runbook and complete the remaining required gates below.

## Canonical procedures

Read `AGENTS.md`, `OVERVIEW.md`, `docs/architecture.md`,
`docs/production-deployment-runbook.md`, `docs/hardware-qualification.md`,
`docs/android-release-signing.md`, and `docs/backup-restore.md`. The production
runbook is the release authority; this file is only the current-state summary.

## Release candidate

- Source: `origin/main` at
  `9d71fe16c6a0a03cb1fb697ed21658df3de1a4f9` (merged PR #48).
- CI: backend, PostgreSQL, Android, Terraform, and ARM64 container build/scan
  jobs passed for that merge.
- Repository governance: `main` has no GitHub branch protection or ruleset.
  CI passed, but GitHub did not enforce it.
- Start release work from a fresh, clean checkout of that exact commit. Do not
  use a dirty workspace, obsolete temporary worktree, or deleted feature branch.

## Current production state (read-only verified 2026-08-21)

| Area | Current state |
| --- | --- |
| Live release | The pre-hardening release remains live; PR #48 is not deployed. |
| Images | No ARM64 image from `9d71fe1` has been pushed to ECR. Production still selects the pre-hardening 2026-08-10 backend digest `sha256:b9a3dc42c985fd28485bc0cc679e8f6c19706e70c14927485728e0caab60d2be`. |
| Infrastructure | The PR #48 Terraform/runtime/worker hardening is unapplied. The host keeps IMDS hop limit 2; the current source requires 1. |
| SSM | AWS has only old v1 runtime-assets and release-config documents. No release-activation document exists. |
| Database | Production migrations `0009`–`0012` have not run. |
| Worker | The old worker task definition and credential path remain deployed. No worker task is currently running. |
| Backup and alarms | DLM and historical logical backup evidence exist, but fresh release-time backup/snapshot evidence is required. The new DLM failure/freshness alarms are absent. |
| Device canary | No exact Android 12+ qualification or real signed-APK `MEETS_DEVICE_INTEGRITY` evidence is recorded. |

The application-secret values were not read. `WORKER_DB_PASSWORD` and
`PLAY_INTEGRITY_APP_CERTIFICATE_SHA256` are required but unverified; never put
their values in this file, source control, logs, or chat.

The old SSM operation `08ccbebf3c7672087b96875e76884479` targets superseded
release documents. Never resume it; use current documents and a fresh operation
ID.

## Recorded release authority

- Cze Yik authorized immediate execution and is the release operator and sole
  rollback decision-maker. No separate maintenance window or backup contact is
  recorded.
- Approved project target: USD 30/month, excluding tablet mobile data.
- Approved one-off deployment/recovery limit: USD 10.
- This authorization does not waive artifact, secret, infrastructure, migration,
  recovery, notification, hardware, integrity, or separate final-activation
  confirmation gates.

## Historical evidence that needs current-release verification

- The available P60 Pro is ineligible for production: Android 10/API 29 with
  the 2021-03-05 platform patch. Do not install the app on it or flash
  unofficial firmware.
- Existing v1.0.0 APK signing evidence, checksums, mapping, and key backups are
  in the encrypted company vault. Build, sign, and verify an artifact matching
  the final release source and `required_app_version` before enrollment.
- A past isolated recovery drill passed and was destroyed. Its header-only CSV
  was valid because the selected source had zero playback events. Treat it as
  historical evidence; rerun and record the current-release recovery gate before
  canary. Never fabricate playback records.

## Required before activation

1. Build, scan, push, and record a Linux/ARM64 backend image from `9d71fe1`.
   Record exact digest-pinned backend, PostgreSQL, and Caddy image selections.
2. Securely add or verify `WORKER_DB_PASSWORD` and
   `PLAY_INTEGRITY_APP_CERTIFICATE_SHA256`; never expose their values.
3. Build and verify the final signed APK. Record its version, checksum,
   certificate fingerprint, and matching `required_app_version` outside Git.
4. Run fresh AWS/owner preflight, make a fresh logical backup and completed DLM
   snapshot, then create and review a fresh saved Terraform plan.
5. Apply only the reviewed plan. It must install the hardened infrastructure,
   runtime/config/activation SSM documents, worker controls, and DLM alarms.
6. Through SSM only, validate then install the current runtime-bundle and
   release-config documents, then validate activation. Activation requires a
   separate explicit `ACTIVATE <operation-id>` confirmation.
7. Before enrollment, complete exact-device Android 12+ qualification, real
   signed-APK `MEETS_DEVICE_INTEGRITY`, notification-delivery testing, recovery
   evidence, and the one-device canary checklist.

## Known source limitation

After the hardening release is applied, the media worker is reduced-privilege
relative to the web tier but is not fully egress- or per-asset-isolated: it
retains access to shared media prefixes and arbitrary HTTPS egress for required
AWS and ClamAV services. Do not claim full containment. The endpoint/proxy and
capability redesign is a separate cost and architecture decision; see
`infrastructure/README.md`.

## Safety boundaries

- No raw Terraform, SSH, hand-edited runtime files, stale plan, manual
  migration/deploy, or device enrollment.
- After a migration starts, do not reverse it or deploy a pre-policy image;
  stop traffic and use a reviewed forward fix or approved recovery path.
- Never reuse old SSM operation IDs, recovery resources, credentials, signing
  material, or browser/DNS trust for testing.
- Never expose secrets, signing material, PINs, tokens, PII, or private media
  URLs in source control, handoff notes, shell history, logs, or chat.

## Next safe action

From a clean checkout of `9d71fe1`, prepare the immutable release artifact and
protected inputs, then run the read-only production preflight. Do not change
production until the resulting plan and SSM validation evidence are reviewed.
