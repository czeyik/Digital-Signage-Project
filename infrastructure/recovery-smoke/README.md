# Isolated Application Restore Smoke

This Terraform root creates one explicitly tagged, disposable recovery host and
one encrypted clone of a selected production DLM snapshot. It exists only to
complete the application-layer restore gate: start the restored Django/Caddy
stack, prove an account-owner login and protected dashboard access, and export
one representative report without routing any public traffic to the clone. It
also verifies one exact versioned normalized-media object against the restored
`MediaAsset` record's key, SHA-256, and size.

It is deliberately separate from `infrastructure/terraform/`. The production
root owns the live EC2 host, Elastic IP, DNS, data volume, and production state
key. Do not add this root's resources to the production state, and never
initialize this directory using `production/terraform.tfstate`.

## Safety properties

- Every resource has a unique 32-hex `OperationId`, `Temporary=true`, and
  `CleanupRequired=true` tag.
- This root is fixed to account `173454940059`, Region `ap-southeast-5`, and
  `Project=duducar-signage`; tfvars cannot retarget those constraints. The
  backend image must resolve to a digest in the fixed production
  `duducar-signage-backend` ECR repository during planning.
- The recovery security group has **zero ingress**. It does not reuse the live
  web security group. Its only egress is DNS to the VPC resolver and HTTPS for
  SSM, AWS APIs, package repositories, and image pulls.
- The temporary host receives an ordinary, ephemeral public IPv4 address only
  because the existing public subnet has no NAT or interface endpoints. It has
  no Elastic IP, Route 53 record, public application listener, SSH access, or
  production security-group attachment. Security groups block all inbound
  traffic; the application is additionally bound to host loopback.
- The instance role can use SSM, pull the exact backend image, read the one
  application secret, and read only the three selected versioned S3 objects:
  logical archive, checksum sidecar, and normalized media sample. It has no
  `s3:Put*`, `s3:Delete*`, `sns:Publish`, ECS run/list/pass-role, or KMS
  encrypt/generate permissions. Because both live buckets use S3 Bucket Keys,
  KMS decrypt is constrained to the exact backup/media bucket encryption
  contexts rather than incorrect object-prefix contexts.
- Docker uses `/var/lib/duducar-recovery/docker` on the encrypted disposable
  root volume. It is masked until after XFS inspection. Before it is unmasked,
  the helper validates the root-owned Docker configuration and rejects local
  systemd replacements/drop-ins that could select another data root or listener.
  Never use the cloned `/srv/duducar/docker`: it can contain production
  `--restart unless-stopped` metadata that would otherwise start cloned
  containers when Docker starts.
- The recovery stack has its own `duducar-recovery-*` names and bridge network,
  no PostgreSQL host port, no systemd DUDU service, no timers, and an internal
  Docker network with no application-container egress. Caddy uses an internal,
  recovery-only certificate and is published only as
  `127.0.0.1:8443` (or the reviewed `recovery_caddy_port`). It never requests a
  production ACME certificate or reuses production Caddy state.
- The rendered application disables SMTP delivery and metadata credentials in
  the container. It does not configure a backup destination, and the IAM role
  cannot write a production backup even if a forbidden command is attempted.

These properties make the smoke safe for clone-only writes such as a Django
login audit event, session, and CSV export audit event. They do not authorize
media upload, preview, password reset, enrollment, playlist publication,
scheduled commands, a fresh backup to the production bucket, or a traffic
cutover.

## Required authority and inputs

Use this root only after the project owner authorizes the short-lived recovery
cost and the selected exact recovery points. The operator must record:

1. a new `operation_id` from `openssl rand -hex 16`;
2. the completed encrypted DLM snapshot ID, source data-volume ID, source KMS
   key, source availability zone, and the required `dlm:managed=true` and
   `DLMBackup=duducar-signage-production-ec2-target-data` tags;
3. the exact logical archive and `.sha256` sidecar S3 keys **and version IDs**;
4. one exact normalized `validated/` media key and version ID, plus the
   non-secret expected `MediaAsset` SHA-256 and size recorded during preflight;
5. the current application secret ARN/KMS key, backup/media bucket names and
   storage KMS key, all obtained without printing secret values;
6. reviewed ARM64 AMI ID, backend/PostgreSQL/Caddy digests, required Android
   version, Play Integrity project number, and CloudFront non-secret settings;
7. the existing VPC and a public subnet in the selected recovery AZ; and
8. a named owner who will enter their password locally, an approver, a two-hour
   stop/cleanup deadline, and a rollback operator.

Do not infer the deployed backend image from the newest ECR tag. Confirm the
digest from the live host's root-owned release configuration in a controlled
read-only session, then record it. Never put a secret value, owner password,
private signing key, or production tfvars file into this repository, command
arguments, terminal transcript, or Terraform input.

## Prepare and review

Create an ignored file from `terraform.tfvars.example`, then run a read-only
account/snapshot/object preflight before any apply. Use
`recovery-terraform` for **every** Terraform command in this root; it fixes and
verifies the remote backend key against the operation ID, while a Terraform
guardrail independently rejects unsafe initialized metadata. Do not run a raw
`terraform plan`, `apply`, or `destroy` here. The wrapper clears inherited
`TF_DATA_DIR`/CLI argument settings and forces the default workspace before it
initializes or accepts a stateful command, so it cannot silently reuse another
directory's backend metadata.

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

The reviewed plan may create only the following resources, all named/tagged
with the operation ID:

- one zero-ingress security group;
- one least-privilege EC2 role and instance profile;
- one encrypted 32 GiB EBS volume created from the stated snapshot;
- one encrypted 16 GiB (or reviewed size) root volume attached to one ARM64
  `t4g.small`; and
- one temporary EC2 instance plus its volume attachment.

Stop if the plan touches a live EC2 instance, existing EBS volume, EIP, DNS,
CloudFront distribution, S3 bucket, KMS key, ECS resource, production IAM role,
or the `production/terraform.tfstate` key. Terraform apply and destroy require
separate, explicit operator approval.

Keep the independently reviewed saved plan as evidence, but never pass it to
`apply`: saved plans embed backend information. The wrapper regenerates a new
private plan only after rechecking its recovery backend/default workspace,
displays it, and asks for its own `APPLY <operation-id>` confirmation. Use the
same explicit variables file, without a positional plan path or
`-auto-approve`:

```sh
"$recovery_tf" apply --operation-id "$operation_id" \
  -var-file=terraform.tfvars
"$recovery_tf" output --operation-id "$operation_id"
```

## Smoke procedure after an approved apply

The recovery host arrives with Docker masked and the clone deliberately
unmounted. Connect only with Session Manager; do not add ingress rules or use
SSH.

```sh
sudo /usr/local/sbin/duducar-recovery-mount inspect
sudo /usr/local/sbin/duducar-recovery-mount mount
sudo /usr/local/sbin/duducar-recovery-mount verify-mounted

# Snapshot path: apply clone-only schema migrations after the snapshot's WAL
# recovery, prove the exact normalized media object, then start the
# internal-TLS dashboard.
sudo /usr/local/sbin/duducar-recovery-restore snapshot-schema
sudo /usr/local/sbin/duducar-recovery-restore media
sudo /usr/local/sbin/duducar-recovery-stack start
sudo /usr/local/sbin/duducar-recovery-stack status
sudo /usr/local/sbin/duducar-recovery-stack tls-info
```

From the local operator workstation, use the non-secret command emitted by
`recovery_ssm_port_forward_command`. The output
`recovery_tls_hostname` is an operation-specific reserved hostname such as
`recovery-<operation-id>.duducar.test`; it has no production DNS record. In a
temporary browser profile, map that exact hostname to `127.0.0.1`, browse it
only on `recovery_tls_port`, and import only the public recovery CA at the host
path emitted by `recovery_tls_ca_path`. Never map or browse a production
hostname through this tunnel.

Retrieve that **public CA only** through the existing Session Manager shell; do
not bypass verification with `curl -k` or a browser click-through. On the
recovery host, run the following after `tls-info`, copy its one-line output,
and compare the local fingerprint to the `tls-info` fingerprint:

```sh
sudo base64 -w0 /run/duducar-recovery/caddy-data/caddy/pki/authorities/local/root.crt
printf '\n'
```

On the local operator workstation, use a temporary file and delete it with the
temporary browser profile after the smoke:

```sh
umask 077
read -r recovery_ca_b64
printf '%s' "$recovery_ca_b64" | base64 -d > "/tmp/duducar-recovery-${operation_id}-ca.crt"
unset recovery_ca_b64
openssl x509 -in "/tmp/duducar-recovery-${operation_id}-ca.crt" -noout -fingerprint -sha256
```

For a non-browser check, use the reserved hostname with `curl --resolve` and
the retrieved CA (never `-k`):

```sh
recovery_host=$("$recovery_tf" output --operation-id "$operation_id" -raw recovery_tls_hostname)
recovery_port=$("$recovery_tf" output --operation-id "$operation_id" -raw recovery_tls_port)
curl --fail --cacert "/tmp/duducar-recovery-${operation_id}-ca.crt" \
  --resolve "${recovery_host}:${recovery_port}:127.0.0.1" \
  "https://${recovery_host}:${recovery_port}/health/ready/"
```

Then perform:

1. one known account-owner login;
2. a protected dashboard request after login; and
3. one representative playback CSV export.

Do not use production DNS for this test. The SSM tunnel plus loopback listener
is the only permitted application path. Record the expected clone-only audit
events and report result, but never copy driver personal data into the change
record.

For the logical backup path, stop the recovery stack, then restore the exact
versioned archive only into the already-disposable cloned database:

```sh
sudo /usr/local/sbin/duducar-recovery-restore logical
sudo /usr/local/sbin/duducar-recovery-restore media
sudo /usr/local/sbin/duducar-recovery-stack start
```

Repeat the same owner-login/dashboard/report smoke. The helper verifies the
sidecar and archive catalogue before dropping the literal `signage` database on
the **clone**. It never touches the source snapshot or production host.

## Cleanup and evidence

After the tests, stop containers, unmount the clone, then use the same isolated
state key and exact `terraform.tfvars` to destroy only this operation.

```sh
sudo /usr/local/sbin/duducar-recovery-stack stop
sudo /usr/local/sbin/duducar-recovery-mount unmount
rm -f "/tmp/duducar-recovery-${operation_id}-ca.crt"
"$recovery_tf" output --operation-id "$operation_id" -raw recovery_cleanup_query
"$recovery_tf" destroy --operation-id "$operation_id" \
  -var-file=terraform.tfvars
```

Before destroy, record the non-secret `recovery_cleanup_query` output. After
destroy, run `"$recovery_tf" cleanup-check --operation-id "$operation_id"`;
it verifies account `173454940059`, taggable temporary resources, and the exact
named IAM role/profile. Record source IDs/versions, start/end times,
owner-login/report/media-proof result, RPO/RTO, cost window,
instance/volume/role/security-group IDs, destruction result, and approver. Do
not delete the DLM source snapshot or the separately retained manual bootstrap
snapshot during this cleanup.
