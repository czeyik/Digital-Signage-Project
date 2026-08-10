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
4. Require an exact Android 12+ device qualification and real signed-APK
   `MEETS_DEVICE_INTEGRITY`; enrollment stays disabled if either is absent.
5. Review `docs/aws-cost-estimate.md`; stop if projected monthly cost exceeds
   USD 30 excluding per-tablet mobile data, unless the owner changes the target.

## Prepare a release

1. Start only from a clean reviewed release worktree. Run applicable backend,
   Android, Terraform and security tests; record results and unresolved risk.
2. Build Linux/ARM64 backend from that commit; scan it and reject unresolved
   HIGH/CRITICAL findings. Push the approved image and select its immutable ECR
   digest—never a tag. If worker code changes, its task definition uses this
   same digest and required IAM (including host `ecs:ListTasks`) before web code.
3. Create and verify a fresh versioned logical backup and a completed DLM data
   snapshot; record exact versions and source. Ensure no isolated worker task
   runs before schema/worker changes.
4. Make a fresh saved Terraform plan using protected production variables.
   Review every action: expected controls retain EC2/private CloudFront/no
   continuous worker and no ECS web/ALB/RDS/NAT/public S3. Explicitly approve
   any backup lifecycle retention/deletion effect. Stop on unexpected changes.
5. Apply the reviewed Terraform plan before host configuration. Never put
   secrets in Terraform, arguments, history, logs or chat; enter them only via
   the approved Secrets Manager path.

## Configure and deploy through SSM

1. Use SSM only; never SSH or hand-edit `/etc/duducar/release.env`, cloud-init,
   or runtime files. From applied Terraform outputs obtain instance, release
   document/version/hash, pinned image digest and app version; verify the
   selected app version matches the signed APK.
2. Generate a fresh 32-hex operation ID. Send the exact release-config document
   with `Mode=validate`, `ExpectedCommit`, `OperationId`, `BackendImage`, and
   `RequiredAppVersion`; wait for `Success`, record, and review the non-secret
   result. If runtime assets changed, validate their pinned SSM document as well
   (use a digest-pinned Caddy image when relevant).
3. Only after successful validation and a fresh explicit confirmation may run:

   ```text
   INSTALL CONFIG <operation-id>
   ```

   Install with the same document version/hash, commit, operation ID, image and
   app version. Require `Success`; record command IDs. The same-input config
   rollback is allowed only before migrations and requires
   `ROLLBACK CONFIG <operation-id>`. Then render without printing secrets and
   verify effective `REQUIRED_APP_VERSION`.
4. Before migration, prove an active owner can log in; notify users that 0009
   signs them out. Before 0010 record active devices/legacy qualifications and
   triage retired-power alerts. Run `duducar-command migrate`, `grant-runtime`,
   and `migration-check` once; 0009 flushes sessions, 0010 invalidates legacy
   qualifications, and 0011 fails closed on unexpected schema.
5. After 0010, do not select a pre-policy image or reverse the migration in
   production: use the selected image or a reviewed forward fix. Deploy the
   pinned stack, require status/readiness, and verify any worker uses its digest.

## Hardware, rehearsal, and canary

1. Store evidence for every test in `hardware-qualification.md`; an owner must
   approve the exact model/firmware `HardwareQualification` before assignment.
   Legacy vehicle-power fields never qualify a device.
2. From outside the host, verify DNS/TLS/redirects/live+ready. Test owner and
   marketing authorization, logout/lockout/reset, driver-name privacy, private
   signed-media denial, JPEG/PNG/MP4 quarantine/normalization/preview, playlist
   behavior, isolated-worker exit, timers/host alarms/backup alerts and budget.
3. Deliberately fail one isolated worker task and require EventBridge/SNS
   delivery to a test inbox. A configured rule is not proof of notification.
4. On exactly one assigned tablet: enroll using a 15-minute code; verify atomic
   sync/hash/playback/fallback, idempotent proof/report/CSV privacy, offline and
   corrupt-download recovery, server time, normal/urgent replacement,
   disable/reactivate, 10-GiB cache/500-MiB loss record, kiosk/PIN, low battery
   escalation, shutdown/neutral screen/visible Resume, and physical recovery.
5. Confirm only full media counts once; a battery/parked play proves neither
   vehicle operation, occupancy, external power nor audience. Run Android-13
   exit diagnostics where applicable and document platform limitations.

## Recovery and decision

Before canary, `backup-restore.md` must show an isolated logical archive,
DLM-clone and exact-media restore within 24 hours, with owner login and report/
CSV export evidence. Header-only CSV is export-path evidence only when the
selected source genuinely has zero playback events; never fabricate records.

**Go** only when CI, image review, owner prerequisites, private media,
notification delivery, restore evidence, cost, integrity and exact-hardware
gates all pass. Otherwise keep vehicle enrollment disabled; dashboard/API-only
operation is the fallback. Expand beyond one device only with separate approval.
