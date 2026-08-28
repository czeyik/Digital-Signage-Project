#!/bin/bash
set -euo pipefail

runtime_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
terraform_dir=$(cd "$runtime_dir/../.." && pwd)
stack="$runtime_dir/duducar-stack"
renderer="$runtime_dir/render-runtime-env"
broker="$runtime_dir/duducar-credential-broker"
backup_verifier="$runtime_dir/duducar-backup-verify"
activation="$runtime_dir/activate-release"
host_health="$runtime_dir/duducar-host-health"

require_literal() {
  literal=$1
  file=$2
  grep -Fq -- "$literal" "$file" || {
    echo "Missing production runtime guardrail in $file: $literal" >&2
    exit 1
  }
}

reject_literal() {
  literal=$1
  file=$2
  if grep -Fq -- "$literal" "$file"; then
    echo "Forbidden production runtime value in $file: $literal" >&2
    exit 1
  fi
}

bash -n \
  "$stack" \
  "$renderer" \
  "$runtime_dir/duducar-command" \
  "$runtime_dir/postgres-init-roles.sh" \
  "$runtime_dir/manage-runtime-assets" \
  "$runtime_dir/manage-release-config" \
  "$activation" \
  "$host_health" \
  "$runtime_dir/test-stopped-backup-refresh.sh" \
  "$runtime_dir/test-activation-recovery.sh"
python3 "$runtime_dir/test-credential-broker.py"
python3 "$runtime_dir/test-backup-verifier.py"
bash "$runtime_dir/test-duducar-stack.sh"
bash "$runtime_dir/test-stopped-backup-refresh.sh"
bash "$runtime_dir/test-activation-recovery.sh"

invalid_host_health_output=$(bash "$host_health" --not-a-valid-option 2>&1 || true)
if [[ "$invalid_host_health_output" != *'Usage: duducar-host-health [--skip-public-https]'* ]]; then
  echo "Host health must reject unknown options before reading host configuration." >&2
  exit 1
fi
recovery_claim_test=$(mktemp -d /tmp/duducar-recovery-claim.XXXXXX)
recovery_claim="$recovery_claim_test/.claim"
mkdir -m 0700 "$recovery_claim"
if mkdir -m 0700 "$recovery_claim" >/dev/null 2>&1; then
  echo "Recovery authorization claims must be exclusive." >&2
  exit 1
fi
rmdir "$recovery_claim" "$recovery_claim_test"

require_literal 'http_put_response_hop_limit = 1' "$terraform_dir/ec2_target.tf"
require_literal 'resource "aws_iam_role" "ec2_target_application"' "$terraform_dir/ec2_target.tf"
require_literal 'Action   = ["sts:AssumeRole"]' "$terraform_dir/ec2_target.tf"
require_literal 'role  = aws_iam_role.ec2_target_application[0].id' "$terraform_dir/ec2_target.tf"
require_literal 'DB_USER", value = "signage_worker"' "$terraform_dir/ec2_target.tf"
require_literal 'WORKER_DB_PASSWORD::' "$terraform_dir/ec2_target.tf"
reject_literal 'DJANGO_SECRET_KEY", valueFrom' "$terraform_dir/ec2_target.tf"
require_literal 'command                = ["sh", "worker-entrypoint-root-init.sh"]' "$terraform_dir/ec2_target.tf"
require_literal 'user                   = "0:0"' "$terraform_dir/ec2_target.tf"
require_literal 'readonlyRootFilesystem = true' "$terraform_dir/ec2_target.tf"
require_literal '{ name = "WORKER_ROOT_INIT", value = "1" }' "$terraform_dir/ec2_target.tf"
require_literal 'if [ "${WORKER_ROOT_INIT:-}" != "1" ]; then' "$terraform_dir/../../backend/worker-entrypoint-root-init.sh"
require_literal 'exec su-exec 10001:10001 ./worker-entrypoint.sh "$@"' "$terraform_dir/../../backend/worker-entrypoint-root-init.sh"
reject_literal 'drop = ["ALL"]' "$terraform_dir/ec2_target.tf"
require_literal 'resource "aws_vpc_security_group_rules_exclusive" "ec2_target_worker"' "$terraform_dir/ec2_target.tf"
require_literal 'Sid      = "DecryptWorkerSecretThroughSecretsManager"' "$terraform_dir/ec2_target.tf"
if [ "$(grep -Fc '"kms:EncryptionContext:SecretARN" = aws_secretsmanager_secret.application.arn' "$terraform_dir/ec2_target.tf")" -lt 2 ]; then
  echo "Host and worker execution KMS decrypt must both be bound to the exact application secret." >&2
  exit 1
fi
require_literal '"${aws_s3_bucket.media.arn}/*"' "$terraform_dir/ec2_target.tf"
require_literal 'Sid      = "ReadQuarantinedMedia"' "$terraform_dir/ec2_target.tf"
require_literal 'Resource = ["${aws_s3_bucket.media.arn}/quarantine/*"]' "$terraform_dir/ec2_target.tf"
require_literal 'Sid      = "ManageValidatedMedia"' "$terraform_dir/ec2_target.tf"
require_literal 'metric_name         = "SnapshotsCreateFailed"' "$terraform_dir/ec2_target.tf"
require_literal 'metric_name = "SnapshotsCreateCompleted"' "$terraform_dir/ec2_target.tf"
require_literal 'expression  = "FILL(completed, 0)"' "$terraform_dir/ec2_target.tf"
require_literal 'alarm:${local.name}-dlm-snapshot-create-failed' "$terraform_dir/main.tf"
require_literal 'alarm:${local.name}-dlm-snapshot-stale' "$terraform_dir/main.tf"
require_literal '"${aws_s3_bucket.backups.arn}/*"' "$terraform_dir/ec2_target.tf"
reject_literal '"${aws_s3_bucket.media.arn}*"' "$terraform_dir/ec2_target.tf"
reject_literal '"${aws_s3_bucket.backups.arn}*"' "$terraform_dir/ec2_target.tf"
require_literal 'hostssl signage   signage_worker  10.40.0.0/16' "$runtime_dir/pg_hba.conf"
reject_literal 'signage_app     10.40.0.0/16' "$runtime_dir/pg_hba.conf"
require_literal 'GRANT UPDATE (' "$runtime_dir/postgres-runtime-grants.sql"
reject_literal 'GRANT SELECT, UPDATE ON TABLE signage_mediaasset' "$runtime_dir/postgres-runtime-grants.sql"

require_literal 'network_subnet=172.30.0.0/24' "$stack"
require_literal 'web_ip=172.30.0.10' "$stack"
require_literal '--ip "$web_ip"' "$stack"
reject_literal '--restart' "$stack"
require_literal 'container_config_matches duducar-postgres "$POSTGRES_IMAGE" "$database_ip"' "$stack"
require_literal 'monitor_stack()' "$stack"
require_literal 'assert_release()' "$stack"
require_literal 'start_container_if_stopped duducar-postgres' "$stack"
require_literal 'start_container_if_stopped duducar-web' "$stack"
require_literal 'start_database false' "$stack"
require_literal 'backup-start' "$stack"
require_literal 'backup-stop' "$stack"
if [ "$(grep -Fc '/run/duducar/backend-secrets:ro' "$stack")" -ne 1 ]; then
  echo "Only the web container may receive the application secret/token mount." >&2
  exit 1
fi

require_literal 'AWS_CONTAINER_CREDENTIALS_FULL_URI http://169.254.170.2:51679/v2/credentials' "$renderer"
reject_literal 'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI' "$renderer"
require_literal 'AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE /run/duducar/backend-secrets/aws-credentials-token' "$renderer"
require_literal 'AWS_EC2_METADATA_DISABLED true' "$renderer"
require_literal 'refusing silent replacement' "$renderer"
require_literal 'BIND_PORT = 51679' "$broker"
reject_literal 'BIND_PORT = 80' "$broker"
require_literal 'BROKER_URL = "http://169.254.170.2:51679/v2/credentials"' "$backup_verifier"
require_literal 'DUDUCAR_BACKUP_ASSUME_ROLE' "$backup_verifier"
require_literal '"sts",' "$backup_verifier"
require_literal 'ExecStartPre=+/usr/local/sbin/render-duducar-runtime-env' "$runtime_dir/duducar-credential-broker.service"
require_literal 'ExecStartPre=+/usr/sbin/ip address replace 169.254.170.2/32 dev lo' "$runtime_dir/duducar-credential-broker.service"
require_literal 'ExecStopPost=-+/usr/sbin/ip address del 169.254.170.2/32 dev lo' "$runtime_dir/duducar-credential-broker.service"
require_literal 'ProtectSystem=strict' "$runtime_dir/duducar-credential-broker.service"
require_literal 'ReadWritePaths=/run/duducar' "$runtime_dir/duducar-credential-broker.service"
require_literal 'RestrictAddressFamilies=AF_INET AF_UNIX AF_NETLINK' "$runtime_dir/duducar-credential-broker.service"
require_literal 'CapabilityBoundingSet=' "$runtime_dir/duducar-credential-broker.service"
reject_literal 'CAP_DAC_OVERRIDE' "$runtime_dir/duducar-credential-broker.service"
reject_literal 'CAP_NET_ADMIN' "$runtime_dir/duducar-credential-broker.service"
reject_literal 'CAP_NET_BIND_SERVICE' "$runtime_dir/duducar-credential-broker.service"
require_literal 'broker_secrets_dir="$runtime_dir/broker-secrets"' "$renderer"
require_literal '"$staging_dir/aws-credentials-token" "$staging_dir/broker-credentials-token"' "$renderer"
require_literal 'mv -f "$staging_dir/broker-credentials-token" "$broker_secrets_dir/aws-credentials-token"' "$renderer"
require_literal 'TOKEN_PATH = "/run/duducar/broker-secrets/aws-credentials-token"' "$broker"
require_literal 'metadata.st_uid != 0 or metadata.st_gid != 0' "$broker"
require_literal 'duducar-credential-broker.service' "$runtime_dir/duducar.service"
require_literal 'ExecStart=/usr/local/sbin/duducar-stack monitor' "$runtime_dir/duducar.service"
reject_literal 'RemainAfterExit=yes' "$runtime_dir/duducar.service"
require_literal 'hmac.compare_digest' "$broker"
require_literal '"sts",' "$broker"
require_literal 'ProxyHandler({})' "$backup_verifier"
reject_literal 'urllib.request.urlopen' "$backup_verifier"
require_literal 'DUDUCAR_BACKUP_OPERATION_ID' "$backup_verifier"
require_literal 'DUDUCAR_BACKUP_EXPECTED_OPERATION_ID' "$backup_verifier"
require_literal 'exit "$status"' "$runtime_dir/duducar-host-health"
require_literal '--skip-public-https' "$host_health"
require_literal 'run_backup_refresh' "$runtime_dir/duducar-command"
require_literal 'duducar-recovery-backup-' "$runtime_dir/duducar-command"
require_literal '--env DEPLOYMENT_COMPONENT=scheduled' "$runtime_dir/duducar-command"
require_literal '--network duducar' "$runtime_dir/duducar-command"
backup_runner=$(sed -n '/^run_backup_refresh()/,/^)$/p' "$runtime_dir/duducar-command")
if grep -Fq -- '--publish' <<< "$backup_runner"; then
  echo "Stopped-state backup runner must not publish a host port." >&2
  exit 1
fi
require_literal 'failed-existing' "$activation"
require_literal 'RECOVER $operation_id FROM $recovery_from_operation_id' "$activation"
require_literal 'ARM $operation_id FROM $recovery_from_operation_id' "$activation"
require_literal 'failed_activation_command_id' "$activation"
require_literal 'recovery_state_max_age=900' "$activation"
require_literal 'consume_recovery_state' "$activation"
require_literal 'mkdir -m 0700 "$recovery_claim_dir"' "$activation"
require_literal 'ensure_failed_existing_backup' "$activation"
require_literal 'refresh_stopped_backup' "$activation"
require_literal '"$stack_command" backup-start' "$activation"
require_literal '"$command_command" backup-refresh "$operation_id"' "$activation"
require_literal 'DUDUCAR_BACKUP_EXPECTED_OPERATION_ID="$operation_id"' "$activation"
require_literal 'Do not consume the arm until' "$activation"
require_literal 'rmdir -- "$recovery_claim_dir"' "$activation"
require_literal '--skip-public-https' "$activation"
require_literal 'systemctl is-enabled "$unit"' "$activation"
require_literal "ss -ltnH '( sport = :80 or sport = :443 )'" "$activation"
require_literal '"$stack_command" stop || status=1' "$activation"
require_literal 'verify_recovery_alert_delivery' "$activation"
require_literal 'duducar-recovery-sns-${operation_id}-${mode}' "$activation"

for asset in \
  duducar-stack \
  duducar.service \
  duducar-credential-broker \
  duducar-credential-broker.service \
  duducar-backup-verify \
  postgres-init-roles.sh \
  postgres-runtime-grants.sql; do
  require_literal "\"$asset\"" "$terraform_dir/runtime_assets.tf"
done
require_literal 'resource "aws_ssm_document" "ec2_release_activation"' "$terraform_dir/release_activation.tf"
require_literal 'RecoveryFromOperationId' "$terraform_dir/release_activation.tf"
require_literal 'FailedActivationCommandId' "$terraform_dir/release_activation.tf"
require_literal 'PostgresImage' "$terraform_dir/release_config_assets.tf"
require_literal 'CaddyImage' "$terraform_dir/release_config_assets.tf"

assert_before() {
  earlier=$1
  later=$2
  file=$3
  earlier_line=$(grep -nF -- "$earlier" "$file" | head -n 1 | cut -d: -f1)
  later_line=$(grep -nF -- "$later" "$file" | head -n 1 | cut -d: -f1)
  [ -n "$earlier_line" ] && [ -n "$later_line" ] && [ "$earlier_line" -lt "$later_line" ] || {
    echo "Expected activation ordering in $file: $earlier before $later" >&2
    exit 1
  }
}
assert_before '"$stack_command" deploy' '"$command_command" migrate' "$activation"
assert_before '"$command_command" migrate' '"$command_command" grant-runtime' "$activation"
assert_before '"$command_command" grant-runtime' 'systemctl start duducar.service' "$activation"
assert_before 'systemctl start duducar.service' '"$stack_command" assert-release' "$activation"
post_backup=$(sed -n '/"$command_command" backup/,$p' "$activation")
if ! grep -Fq '"$host_health_command"' <<< "$post_backup"; then
  echo "Activation must verify public host health after its post-cutover backup." >&2
  exit 1
fi
shutdown=$(sed -n '/^traffic_changed=1$/,+1p' "$activation")
if [[ "$shutdown" != *'systemctl stop "${timers[@]}" duducar.service duducar-credential-broker.service'* ]]; then
  echo "Activation must arm fail-closed cleanup before it stops production traffic." >&2
  exit 1
fi
deploy_body=$(sed -n '/^deploy_stack()/,/^}/p' "$stack")
if grep -Fq 'create_caddy' <<< "$deploy_body"; then
  echo "Deployment must keep Caddy absent until systemd activation." >&2
  exit 1
fi

echo 'Production runtime credential, lifecycle, and worker guardrails passed.'
