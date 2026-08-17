#!/bin/bash
set -euo pipefail
umask 0077

terraform_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
runtime_dir=$terraform_dir/ec2/runtime
work_dir=$(mktemp -d /tmp/duducar-production-user-data.XXXXXX)
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT
rendered=$work_dir/bootstrap.sh
cp "$terraform_dir/ec2/bootstrap.sh.tftpl" "$rendered"

replace_placeholder() {
  name=$1
  value=$2
  sed -i "s|\${$name}|$value|g" "$rendered"
}

asset_placeholders=(
  caddyfile_post_cutover_b64:Caddyfile.post-cutover
  postgres_hba_b64:pg_hba.conf
  postgres_init_roles_b64:postgres-init-roles.sh
  postgres_runtime_grants_b64:postgres-runtime-grants.sql
  render_env_b64:render-runtime-env
  stack_b64:duducar-stack
  command_b64:duducar-command
  alert_b64:duducar-alert
  host_health_b64:duducar-host-health
  backup_verify_b64:duducar-backup-verify
  credential_broker_b64:duducar-credential-broker
  service_b64:duducar.service
  credential_broker_service_b64:duducar-credential-broker.service
  command_service_b64:duducar-command@.service
  alert_service_b64:duducar-alert@.service
  health_timer_b64:duducar-health.timer
  playlist_timer_b64:duducar-playlists.timer
  reconcile_timer_b64:duducar-media-reconcile.timer
  retention_timer_b64:duducar-retention.timer
  backup_timer_b64:duducar-backup.timer
)
for mapping in "${asset_placeholders[@]}"; do
  name=${mapping%%:*}
  source=${mapping#*:}
  replace_placeholder "$name" "$(base64 -w0 "$runtime_dir/$source")"
done

sample=0123456789abcdef0123456789abcdef0123456789abcdef
replace_placeholder aws_region ap-southeast-5
replace_placeholder application_secret_arn "arn:aws:secretsmanager:ap-southeast-5:173454940059:secret:duducar-$sample"
replace_placeholder application_role_arn "arn:aws:iam::173454940059:role/duducar-signage-production-application"
replace_placeholder media_bucket "duducar-signage-production-media-$sample"
replace_placeholder backup_bucket "duducar-signage-production-backups-$sample"
replace_placeholder dashboard_hostname marketing.duducaradmin.com
replace_placeholder api_hostname api.marketing.duducaradmin.com
replace_placeholder required_app_version 1.0.0
replace_placeholder backend_image "173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:${sample}0123456789abcdef"
replace_placeholder postgres_image "postgres@sha256:${sample}0123456789abcdef"
replace_placeholder caddy_image "caddy@sha256:${sample}0123456789abcdef"
replace_placeholder play_integrity_project 123456789012
replace_placeholder smtp_host smtp.example.duducar.co
replace_placeholder smtp_port 587
replace_placeholder default_from_email operations@duducar.co
replace_placeholder operations_sns_topic "arn:aws:sns:ap-southeast-5:173454940059:duducar-signage-production-operations"
replace_placeholder data_volume_id vol0123456789abcdef0
replace_placeholder data_volume_api_id vol-0123456789abcdef0
replace_placeholder ecs_cluster "arn:aws:ecs:ap-southeast-5:173454940059:cluster/duducar-signage-production"
replace_placeholder ecs_worker_task_definition duducar-signage-production-ec2-media-worker
replace_placeholder ecs_media_subnet_ids subnet-0123456789abcdef0,subnet-abcdef01234567890
replace_placeholder ecs_media_security_group_ids sg-0123456789abcdef0
replace_placeholder cloudfront_domain d0123456789abcdef.cloudfront.net
replace_placeholder cloudfront_public_key_id K0123456789ABCDEF
replace_placeholder media_processing_lease 1500
replace_placeholder media_dispatch_retry 60
replace_placeholder media_max_dispatch_attempts 3
replace_placeholder media_reconcile_max_assets 25

if rg -q '\$\{[A-Za-z_][A-Za-z0-9_]*\}' "$rendered"; then
  echo "Production bootstrap still contains an unrendered Terraform placeholder." >&2
  exit 1
fi
bash -n "$rendered"
raw_user_data_bytes=$(gzip -n -c "$rendered" | wc -c | tr -d '[:space:]')
max_raw_user_data_bytes=16000
if [ "$raw_user_data_bytes" -gt "$max_raw_user_data_bytes" ]; then
  echo "Production EC2 raw user data is ${raw_user_data_bytes} bytes; budget is ${max_raw_user_data_bytes} bytes (EC2 limit 16384)." >&2
  exit 1
fi
echo "Production bootstrap user-data check passed: ${raw_user_data_bytes} bytes (budget ${max_raw_user_data_bytes}, EC2 limit 16384)."
