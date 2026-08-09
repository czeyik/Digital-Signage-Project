output "recovery_operation_id" {
  description = "Recorded operation ID that must match every temporary-resource tag and state key."
  value       = var.operation_id
}

output "recovery_instance_id" {
  description = "Temporary recovery EC2 instance. Access it only through Session Manager port forwarding."
  value       = aws_instance.recovery.id
}

output "recovery_volume_id" {
  description = "Disposable encrypted volume cloned from the selected DLM snapshot."
  value       = aws_ebs_volume.recovery_data.id
}

output "recovery_security_group_id" {
  description = "Temporary zero-ingress recovery security group."
  value       = aws_security_group.recovery.id
}

output "recovery_instance_profile_name" {
  description = "Temporary least-privilege instance profile; it has no production write permissions."
  value       = aws_iam_instance_profile.recovery.name
}

output "recovery_selected_inputs" {
  description = "Non-secret recovery evidence to record with the smoke result."
  value = {
    source_snapshot_id         = var.source_snapshot_id
    source_data_volume_id      = var.source_data_volume_id
    source_archive_key         = var.source_archive_key
    source_archive_version_id  = var.source_archive_version_id
    source_sidecar_key         = var.source_sidecar_key
    source_sidecar_version_id  = var.source_sidecar_version_id
    source_media_key           = var.source_media_key
    source_media_version_id    = var.source_media_version_id
    source_media_sha256        = var.source_media_sha256
    source_media_size_bytes    = var.source_media_size_bytes
    recovery_availability_zone = data.aws_subnet.recovery.availability_zone
    recovery_caddy_port        = var.recovery_caddy_port
    recovery_tls_hostname      = local.recovery_hostname
  }
}

output "recovery_tls_hostname" {
  description = "Reserved operation-specific hostname used only through the local SSM tunnel and recovery-only Caddy certificate; it has no production DNS record."
  value       = local.recovery_hostname
}

output "recovery_tls_port" {
  description = "Loopback-only recovery Caddy TLS port forwarded through Session Manager."
  value       = var.recovery_caddy_port
}

output "recovery_tls_ca_path" {
  description = "Public recovery-only Caddy root CA path on the temporary host after duducar-recovery-stack start; import it only into a temporary browser profile."
  value       = "/run/duducar-recovery/caddy-data/caddy/pki/authorities/local/root.crt"
}

output "recovery_ssm_port_forward_command" {
  description = "Start this local tunnel only after the recovery stack reports healthy. The Caddy listener remains bound to host loopback."
  value = format(
    "aws ssm start-session --profile dudu-production --region %s --target %s --document-name AWS-StartPortForwardingSession --parameters portNumber=%q,localPortNumber=%q",
    local.production_region,
    aws_instance.recovery.id,
    tostring(var.recovery_caddy_port),
    tostring(var.recovery_caddy_port),
  )
}

output "recovery_cleanup_query" {
  description = "Read-only post-destroy query for taggable resources. recovery-terraform cleanup-check additionally verifies the guarded account and named IAM role/profile absence."
  value = format(
    "aws resourcegroupstaggingapi get-resources --profile dudu-production --region %s --tag-filters Key=OperationId,Values=%s --query 'ResourceTagMappingList[].ResourceARN' --output text",
    local.production_region,
    var.operation_id,
  )
}
