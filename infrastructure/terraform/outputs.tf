output "ecr_repository_url" {
  description = "Backend image repository used by the EC2 host release and isolated Fargate media worker."
  value       = aws_ecr_repository.backend.repository_url
}

output "application_secret_arn" {
  description = "Runtime secret read by the EC2 host and isolated media worker."
  value       = aws_secretsmanager_secret.application.arn
}

output "database_secret_arn" {
  description = "Retired RDS-managed secret. Null after legacy RDS decommission; current PostgreSQL uses the application secret plus host-local owner credentials."
  value       = try(aws_db_instance.production[0].master_user_secret[0].secret_arn, null)
  sensitive   = true
}

output "load_balancer_dns_name" {
  description = "Retired ALB DNS name. Null in current EC2 production."
  value       = try(aws_lb.production[0].dns_name, null)
}

output "media_bucket" {
  description = "Private media bucket; validated objects are delivered through signed CloudFront URLs."
  value       = aws_s3_bucket.media.bucket
}

output "backup_bucket" {
  description = "Private destination for current EC2-hosted PostgreSQL logical backups."
  value       = aws_s3_bucket.backups.bucket
}

output "ecs_cluster" {
  description = "Retained ECS cluster used only for isolated, one-off Fargate media-worker tasks."
  value       = aws_ecs_cluster.production.name
}

output "public_subnet_ids" {
  description = "Public subnets used by the EC2 host and isolated Fargate media tasks."
  value       = aws_subnet.public[*].id
}

output "web_security_group_id" {
  description = "Retired ECS web security group retained for Terraform state compatibility; not used by current production."
  value       = aws_security_group.web.id
}

output "task_security_group_id" {
  description = "Retired RDS-backed task security group retained for Terraform state compatibility; not used by the current media worker."
  value       = aws_security_group.tasks.id
}

output "application_task_definition" {
  description = "Retired ECS web task definition. Null after legacy RDS decommission; do not use for production commands."
  value       = try(aws_ecs_task_definition.application[0].arn, null)
}

output "scheduled_task_definition" {
  description = "Retired EventBridge task definition. Null after decommission; current schedules run under systemd on EC2."
  value       = try(aws_ecs_task_definition.scheduled[0].arn, null)
}

output "operations_sns_topic" {
  description = "SNS topic for current host, task, and budget operations alerts."
  value       = aws_sns_topic.operations.arn
}

output "ec2_target_instance_id" {
  description = "Migration-era alias for the live production host instance ID. Connect through SSM only."
  value       = try(aws_instance.ec2_target[0].id, null)
}

output "ec2_target_public_ip" {
  description = "Migration-era alias for the live production Elastic IP used by Route 53 application records."
  value       = try(aws_eip.ec2_target[0].public_ip, null)
}

output "ec2_target_private_ip" {
  description = "Migration-era alias for the production PostgreSQL endpoint allowed only from the isolated media-worker security group."
  value       = try(aws_network_interface.ec2_target[0].private_ip, null)
}

output "ec2_target_data_volume_id" {
  description = "Migration-era alias for the live encrypted 32 GB production data volume protected by DLM."
  value       = try(aws_ebs_volume.ec2_target_data[0].id, null)
}

output "ec2_target_worker_task_definition" {
  description = "Current isolated, one-off Fargate media-worker family using the EC2-hosted PostgreSQL database."
  value       = try(aws_ecs_task_definition.ec2_target_worker[0].arn, null)
}

output "production_host_instance_id" {
  description = "Live USD 30 production host instance ID. Connect through SSM only."
  value       = try(aws_instance.ec2_target[0].id, null)
}

output "production_host_public_ip" {
  description = "Live production Elastic IP used by Route 53 application records."
  value       = try(aws_eip.ec2_target[0].public_ip, null)
}

output "production_host_private_ip" {
  description = "Private production PostgreSQL endpoint allowed only from the isolated media-worker security group."
  value       = try(aws_network_interface.ec2_target[0].private_ip, null)
}

output "production_data_volume_id" {
  description = "Live encrypted 32 GB production data volume protected by DLM."
  value       = try(aws_ebs_volume.ec2_target_data[0].id, null)
}

output "media_worker_task_definition" {
  description = "Current isolated, one-off Fargate media-worker task definition."
  value       = try(aws_ecs_task_definition.ec2_target_worker[0].arn, null)
}

output "media_cloudfront_domain" {
  description = "Private signed-URL distribution domain for validated media."
  value       = try(aws_cloudfront_distribution.media[0].domain_name, null)
}

output "media_cloudfront_public_key_id" {
  description = "Public key ID used when the application signs CloudFront URLs."
  value       = try(aws_cloudfront_public_key.media[0].id, null)
}

output "production_runtime_asset_document" {
  description = "SSM document for explicitly validated EC2 runtime asset updates."
  value       = try(aws_ssm_document.ec2_runtime_assets[0].name, null)
}

output "production_runtime_asset_document_version" {
  description = "Exact SSM document version to pin during runtime asset validation and installation."
  value       = try(aws_ssm_document.ec2_runtime_assets[0].document_version, null)
}

output "production_runtime_asset_document_hash" {
  description = "AWS-generated hash to pin during runtime asset validation and installation."
  value       = try(aws_ssm_document.ec2_runtime_assets[0].hash, null)
}

output "production_runtime_asset_document_hash_type" {
  description = "Hash algorithm for the pinned runtime asset SSM document."
  value       = try(aws_ssm_document.ec2_runtime_assets[0].hash_type, null)
}

output "production_runtime_asset_sha256" {
  description = "Reviewed SHA-256 values embedded in the runtime asset document."
  value = {
    caddyfile          = local.ec2_runtime_caddy_sha256
    environment_render = local.ec2_runtime_renderer_sha256
    operation_manager  = local.ec2_runtime_manager_sha256
  }
}
