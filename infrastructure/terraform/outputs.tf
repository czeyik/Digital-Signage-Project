output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "application_secret_arn" {
  value = aws_secretsmanager_secret.application.arn
}

output "database_secret_arn" {
  value     = aws_db_instance.production.master_user_secret[0].secret_arn
  sensitive = true
}

output "load_balancer_dns_name" {
  value = aws_lb.production.dns_name
}

output "media_bucket" {
  value = aws_s3_bucket.media.bucket
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "ecs_cluster" {
  value = aws_ecs_cluster.production.name
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "web_security_group_id" {
  value = aws_security_group.web.id
}

output "task_security_group_id" {
  value = aws_security_group.tasks.id
}

output "application_task_definition" {
  value = var.container_image != "" ? aws_ecs_task_definition.application[0].arn : null
}

output "operations_sns_topic" {
  value = aws_sns_topic.operations.arn
}
