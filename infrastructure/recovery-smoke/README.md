# Isolated Application Restore Smoke

This Terraform root creates one tagged recovery host and one encrypted clone of
a selected production DLM snapshot. It validates a snapshot restore, an exact
logical archive, one exact normalized-media object, owner login, protected
dashboard access, and a representative playback CSV without public routing.

It is separate from `infrastructure/terraform/`. Never place recovery resources
in production state or initialize this root with
`production/terraform.tfstate`. The authoritative control sequence is
[`docs/backup-restore.md`](../../docs/backup-restore.md); this page contains the
exact recovery-root commands.

## Safety boundary

- Every resource uses a fresh 32-hex `OperationId` and the tags
  `Temporary=true` and `CleanupRequired=true`.
- Account `173454940059`, Region `ap-southeast-5`, project tag, recovery
  hostname, and backend ECR repository are fixed in code. The backend image
  must be an approved digest from that repository.
- The temporary security group has zero ingress. The host has no EIP, DNS,
  public application listener, SSH, or production security-group attachment;
  its ephemeral public address supports outbound HTTPS only. Administration is
  through SSM.
- The role may use SSM, pull approved images, and read only the selected secret
  and versioned backup/media objects. It cannot write S3, publish SNS, run ECS,
  or mutate production KMS, IAM, database, snapshot, or application resources.
- Recovery Docker uses `/var/lib/duducar-recovery/docker`, never the cloned
  `/srv/duducar/docker`. Docker remains masked until mount checks pass.
- Recovery containers use separate names, a recovery-only network with no app
  egress, no PostgreSQL host port, no timers, and a TLS listener bound to
  `127.0.0.1`. SMTP, metadata credentials, backups, media dispatch, alerts, and
  other production writes are disabled.

Clone-only login, session, and export audit rows are expected. The drill does
not authorize upload, preview, password reset, enrollment, publication,
scheduled commands, backup creation, or traffic cutover.

## Required authority and inputs

Obtain owner approval for the exact recovery points and temporary cost. Record:

1. a new operation ID from `openssl rand -hex 16`;
2. the completed encrypted DLM snapshot, source volume/KMS key/AZ, and required
   DLM tags;
3. exact archive and sidecar keys and S3 version IDs;
4. one exact `validated/` media key/version plus its expected `MediaAsset`
   SHA-256 and size;
5. current secret/KMS ARNs and backup/media bucket names, without secret values;
6. reviewed ARM64 AMI and backend/PostgreSQL/Caddy digests, Android version,
   Play Integrity project number, and non-secret CloudFront settings;
7. the existing VPC and a public subnet in the recovery AZ; and
8. owner, approver, rollback operator, and a two-hour cleanup deadline.

Confirm the deployed backend digest from the live root-owned release
configuration, never from the newest ECR tag. Keep secrets, passwords, signing
keys, production tfvars, and personal data out of Git, arguments, transcripts,
and Terraform inputs.

## Prepare and review

Create an ignored `terraform.tfvars` from the example. Use the wrapper for every
command; it fixes the backend key, clears inherited Terraform indirection, and
requires the default workspace.

```sh
operation_id=$(openssl rand -hex 16)
recovery_tf=./infrastructure/recovery-smoke/recovery-terraform
"$recovery_tf" init --operation-id "$operation_id"
"$recovery_tf" fmt --operation-id "$operation_id" -check
"$recovery_tf" validate --operation-id "$operation_id"
"$recovery_tf" plan --operation-id "$operation_id" \
  -var-file=terraform.tfvars \
  -out="/secure/duducar-recovery/recovery-${operation_id}.tfplan"
"$recovery_tf" show --operation-id "$operation_id" \
  "/secure/duducar-recovery/recovery-${operation_id}.tfplan"
```

The plan may create only one operation-tagged zero-ingress security group, one
least-privilege role/profile, one encrypted 32-GiB snapshot clone, one reviewed
root volume, and one ARM64 `t4g.small`. Stop if it touches a live instance,
volume, EIP, DNS, CloudFront, S3, KMS, ECS, production IAM, or production state.

Retain the reviewed saved plan as evidence but do not apply it: saved plans
embed backend data. The wrapper regenerates and displays a private plan, then
requires `APPLY <operation-id>`:

```sh
"$recovery_tf" apply --operation-id "$operation_id" \
  -var-file=terraform.tfvars
"$recovery_tf" output --operation-id "$operation_id"
```

Apply does not authorize cutover, DNS, source deletion, or production writes.

## Restore and smoke

Connect through SSM only. The host starts with Docker masked and the clone
unmounted. Inspect it only through the helper:

```sh
sudo /usr/local/sbin/duducar-recovery-mount inspect
```

If inspection exits `0`, run:

```sh
sudo /usr/local/sbin/duducar-recovery-mount mount
sudo /usr/local/sbin/duducar-recovery-mount verify-mounted
sudo /usr/local/sbin/duducar-recovery-restore snapshot-schema
sudo /usr/local/sbin/duducar-recovery-restore media
sudo /usr/local/sbin/duducar-recovery-stack start
sudo /usr/local/sbin/duducar-recovery-stack status
sudo /usr/local/sbin/duducar-recovery-stack tls-info
```

If inspection exits `3` with only the recognized dirty-journal condition,
review its root-only diagnostic and run the operation-bound replay:

```sh
sudo /usr/local/sbin/duducar-recovery-mount replay-journal \
  --confirm "REPLAY-JOURNAL <operation-id>"
sudo /usr/local/sbin/duducar-recovery-mount mount
sudo /usr/local/sbin/duducar-recovery-mount verify-mounted
```

The helper validates the receipt, clone/source identity, host architecture,
Docker state, and mount state; replays only the disposable clone; and requires
a clean post-replay check. Never direct-mount the device, add it to `/etc/fstab`,
use a generic repair, or run `xfs_repair -L`. Any other failure stops the drill;
preserve diagnostics and use a fresh operation only after review.

Use the `recovery_ssm_port_forward_command` output from the workstation. Map
only the operation-specific `.test` hostname to `127.0.0.1` in a temporary
browser profile. Do not alter production DNS/system trust, use a production
hostname, expose a port, use HTTP, pass `-k`, or accept a certificate warning.

After `tls-info`, retrieve only the public recovery CA through SSM:

```sh
sudo base64 -w0 /run/duducar-recovery/caddy-data/caddy/pki/authorities/local/root.crt
printf '\n'
```

Decode it locally and match its SHA-256 fingerprint to `tls-info`:

```sh
umask 077
read -r recovery_ca_b64
printf '%s' "$recovery_ca_b64" | base64 -d > "/tmp/duducar-recovery-${operation_id}-ca.crt"
unset recovery_ca_b64
openssl x509 -in "/tmp/duducar-recovery-${operation_id}-ca.crt" \
  -noout -fingerprint -sha256
```

Verify readiness through the tunnel:

```sh
recovery_host=$("$recovery_tf" output --operation-id "$operation_id" -raw recovery_tls_hostname)
recovery_port=$("$recovery_tf" output --operation-id "$operation_id" -raw recovery_tls_port)
curl --fail --cacert "/tmp/duducar-recovery-${operation_id}-ca.crt" \
  --resolve "${recovery_host}:${recovery_port}:127.0.0.1" \
  "https://${recovery_host}:${recovery_port}/health/ready/"
```

In the temporary profile, verify one owner login, a protected dashboard page,
one unfiltered playback CSV, logout, and the protected redirect. A header-only
CSV proves the export path only when the source truly has zero playback events.
Never seed evidence, retain CSV values, or copy driver PII into the record.

Next test the logical archive against the same disposable clone:

```sh
sudo /usr/local/sbin/duducar-recovery-stack stop
sudo /usr/local/sbin/duducar-recovery-restore logical
sudo /usr/local/sbin/duducar-recovery-restore media
sudo /usr/local/sbin/duducar-recovery-stack start
```

Repeat the owner/dashboard/report smoke. The helper verifies the exact sidecar
and catalogue before recreating only the clone's literal `signage` database.

## Cleanup and evidence

Stop, unmount, remove the temporary CA/profile, and destroy only this operation:

```sh
sudo /usr/local/sbin/duducar-recovery-stack stop
sudo /usr/local/sbin/duducar-recovery-mount unmount
rm -f "/tmp/duducar-recovery-${operation_id}-ca.crt"
"$recovery_tf" destroy --operation-id "$operation_id" \
  -var-file=terraform.tfvars
"$recovery_tf" cleanup-check --operation-id "$operation_id"
```

Destroy requires `DESTROY <operation-id>`. `cleanup-check` must confirm that no
recovery instance, clone, security group, role, or profile remains; do not use
the historical Resource Groups Tagging index as deletion proof. Retain the
empty state path and redacted evidence: source IDs/versions, operation ID,
timings/RPO/RTO, temporary resource IDs, TLS/loopback checks, aggregate/media
comparison, owner/report/CSV result, cost, cleanup result, and approver. Never
delete a source snapshot or retained bootstrap snapshot during drill cleanup.
