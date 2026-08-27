# DUDU Car — Production Recovery and Canary Handoff

**Updated:** 2026-08-25 (Asia/Kuala_Lumpur)
**Overall state:** Production is intentionally fail-closed and stopped. Recovery is fully validated but not yet armed or executed. The remaining work is a controlled recovery followed by one-tablet canary enrollment.

## Read first

- Read `AGENTS.md` and the repository's production/runbook documentation before changing anything.
- The canonical merged source is `origin/main` at `196655d91aa3466c9697a6cc4a8f887225e32edf` (PR #66).
- The primary workspace was left on an obsolete branch whose upstream is gone. Do **not** reset or overwrite it. Create/use a clean worktree from `origin/main` for code or infrastructure work.
- This handoff file is intentionally uncommitted in the primary workspace so it can be passed directly to the next agent.

## Completed and merged

| PR | Result |
| --- | --- |
| #57 | Merged — production/canary qualification work. |
| #58 | Merged — supporting qualification/release work. |
| #59 | Merged — physical approval checks no longer block production enrollment. |
| #65 | Merged — narrow, fail-closed recovery for a stopped activation, with replay protection. |
| #66 | Merged — least-privilege KMS permissions for encrypted SNS host alerts. |

PR #66 was applied successfully to production. Terraform changed only the EC2 target-role policy and the existing KMS key policy (`0 added, 2 changed, 0 destroyed`). Its Terraform checks, portable SNS policy check, and CI checks passed.

## Production recovery state

- AWS account/region: `173454940059` / `ap-southeast-5`
- Production instance: `i-0f814d6d80f175319`
- Encrypted operations topic: `duducar-signage-production-operations`
- KMS key: `arn:aws:kms:ap-southeast-5:173454940059:key/90e704a2-0898-42df-b110-436f78e06fd2`
- IAM simulation proved the host role can use the key only through SNS for that exact topic; the key policy authorizes the SNS service principal with the same account/topic restriction.
- The reviewed recovery runtime and activation documents were installed and validated. Post-KMS validation command `93626e7a-112e-442a-9210-6b54a913c4e5` succeeded at `2026-08-25T12:15:04Z` (20:15 MYT), including the encrypted SNS problem-and-clear preflight.
- The most recent read-only host check, command `74d6e1b7-6972-46ef-96c8-2109f3c9b2a4`, completed at `2026-08-25T13:34:47.888Z` (21:34 MYT): `duducar.service` and `duducar-credential-broker.service` were inactive, no containers were running, and there were no listeners. This is the intentional fail-closed state; production is not serving traffic.

The original failed activation was operation `83837d450b89933797aa84665c9ef3b0` (SSM command `c39ef5fa-f918-42e7-b6aa-b16defb30923`). It stopped traffic before deployment/migration/start; no application data migration or service start occurred.

## Immediate blocker: fresh two-step recovery authorization

Recovery is prepared as operation `450eb21325001afc817a8c33b13a4f3d`, correlated to the failed operation above.

Do **not** reuse the earlier `ACTIVATE` messages or old activation codes. Before taking any traffic-affecting action, obtain this exact new user confirmation:

```text
ARM 450eb21325001afc817a8c33b13a4f3d FROM 83837d450b89933797aa84665c9ef3b0
```

`ARM` only arms the reviewed recovery; it does not restore traffic. After it succeeds, report the result and separately request this new confirmation immediately before the traffic transition:

```text
RECOVER 450eb21325001afc817a8c33b13a4f3d FROM 83837d450b89933797aa84665c9ef3b0
```

The user's current authorized maintenance window is 2026-08-25 16:00 MYT through 2026-08-26 16:00 MYT. Confirm the current time remains inside that window before executing recovery.

### Recovery safety boundaries

- Use the reviewed SSM recovery document only. Do not use SSH or manually start services, containers, timers, migrations, or traffic.
- Do not make raw Terraform changes to work around recovery.
- Never retrieve, print, or copy application secret values.
- Preserve fail-closed behavior if any reviewed gate fails; report the precise failed gate instead of bypassing it.

## After a successful recovery

Verify, with fresh read-only checks:

1. External DNS, TLS, and application health are live and ready.
2. The intended release is running; systemd services/timers, credential broker, containers, and listeners are healthy.
3. Backup verification remains successful and no error alert remains open.
4. The controlled recovery ran once only and did not replay an old operation.

Expected immutable production images:

- Backend: `173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:2c67ecc60a47841793f6c2b7f8f3e62ba51161690d9b5db30de8bebfe0aa66d6`
- Postgres: `173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:b7d92c20f54f0d16243a64db68a04765a2f9da47d88c32c14755b396651ccdae`
- Caddy: `173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:e7017ad14a0e5643795d479cab97e468472f23d8987015a0b5069c62703dd81e`

## One-device canary status

The approved Android artifact is available locally:

- APK: `/home/czeyik/dudu-signage-1.0.1-859d564.apk`
- SHA-256: `3319b7b8168296d915b407b9c084d8f3278237745612b25d0592cee1bd3b064b`
- Package/version: `com.duducar.signage`, `1.0.1` (versionCode `2`)
- Signature verification and package metadata checks passed.

The expected tablet is NDL-L09 / HNNDL-Q, Android 16/API 36, firmware `NDL-L09 10.0.0.160(C636E2R2P1)`, security patch `2026-07-01`. The owner attested a 10.95-inch physical display measurement excluding the bezel on 2026-08-24.

Do not assume the tablet is currently connected, still factory-reset, or ADB-authorized. Ask the owner to connect USB and freshly authorize ADB, then collect and compare the actual identifiers before installing or assigning device owner. Device-owner setup must only occur while the device is at factory setup/reset state.

### Simplified qualification policy

The owner authorized the simplified policy: physical pass fields and
`evidence_reference` are optional observations, and no vendor advisory or
patch-age threshold is applied. Exact model, firmware, and security-patch
identity, Android 12+, device-owner provisioning, and signed-APK Play Integrity
remain enrollment gates. Do not fabricate optional observations.

## Remaining safe sequence

1. Obtain exact `ARM` confirmation, execute the reviewed arm action, and report its result.
2. Obtain a separate exact `RECOVER` confirmation, execute the reviewed recovery, and complete the post-recovery checks above.
3. Use the simplified qualification record without fabricating optional evidence.
4. With fresh USB/ADB authorization on the factory-reset tablet, validate identifiers, install the verified APK, set the approved device owner, and verify exact-alarm permission/owner state.
5. Use the production admin workflow to assign the qualified device and issue a short-lived enrollment code.
6. Enroll exactly one tablet and run the canary gates: enrollment, Play Integrity result, API/sync, asset hash/playback, fallback, and observed production health/alerts.

## Useful local paths and environment

- Primary workspace: `/home/czeyik/Documents/Digital Signage Project/Digital-Signage-Project`
- Terraform variables: `infrastructure/terraform/terraform.tfvars`
- AWS environment convention: `AWS_PROFILE=dudu-production` and `AWS_SDK_LOAD_CONFIG=1`
- ADB convention: `/home/czeyik/Android/Sdk/platform-tools/adb`, `ADB_SERVER_PORT=5038`, `ADB_VENDOR_KEYS=/home/czeyik/.android`

Do not expose environment-file, keystore, or application-secret contents in logs, chat, commits, or handoff updates.
