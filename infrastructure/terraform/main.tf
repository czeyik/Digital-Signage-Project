data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

data "aws_route53_zone" "primary" {
  name         = var.domain_name
  private_zone = false
}

# State-compatibility boundary: current production uses EC2, local PostgreSQL,
# CloudFront/S3, and the isolated ec2-media-worker task. The legacy resources
# below remain gated definitions only so old state can be audited safely.
# Never infer the live topology from migration-era resource names.
locals {
  name                                = var.project_name
  azs                                 = slice(data.aws_availability_zones.available.names, 0, 2)
  legacy_ecs_runtime_enabled          = var.enable_services && var.enable_legacy_ecs_runtime
  legacy_ecs_task_definitions_enabled = var.container_image != "" && var.enable_legacy_rds
  legacy_rds_final_snapshot_identifier = (
    trimspace(var.legacy_rds_final_snapshot_identifier) != ""
    ? trimspace(var.legacy_rds_final_snapshot_identifier)
    : "${var.project_name}-final"
  )
  ec2_alarm_arns = [
    "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.name}-ec2-target-status",
    "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.name}-ec2-target-high-cpu",
    "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.name}-ec2-target-low-cpu-credits",
    "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.name}-dlm-snapshot-create-failed",
    "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${local.name}-dlm-snapshot-stale"
  ]
  operations_sns_topic_arn = "arn:${data.aws_partition.current.partition}:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.name}-operations"
  shared_environment = [
    { name = "DJANGO_DEBUG", value = "false" },
    { name = "DEPLOYMENT_ENV", value = "production" },
    { name = "DJANGO_ALLOWED_HOSTS", value = "${var.dashboard_hostname},${var.api_hostname}" },
    { name = "DJANGO_CSRF_TRUSTED_ORIGINS", value = "https://${var.dashboard_hostname}" },
    { name = "OPENMAPTILES_STYLE_URL", value = "/locations/style.json" },
    { name = "OPENMAPTILES_MBTILES_PATH", value = var.openmaptiles_mbtiles_path },
    { name = "DJANGO_SECURE_SSL_REDIRECT", value = "true" },
    { name = "DJANGO_TRUST_X_FORWARDED_PROTO", value = "true" },
    { name = "DJANGO_USE_X_FORWARDED_HOST", value = "false" },
    { name = "AWS_STORAGE_BUCKET_NAME", value = aws_s3_bucket.media.bucket },
    { name = "AWS_S3_REGION_NAME", value = var.aws_region },
    { name = "PILOT_BACKUP_S3_BUCKET", value = aws_s3_bucket.backups.bucket },
    # S3 keeps 30 days via lifecycle; the shared 32 GB host volume keeps a
    # bounded three-archive scratch cache after each verified upload.
    { name = "PILOT_BACKUP_RETENTION_DAYS", value = "3" },
    { name = "PILOT_BACKUP_MAX_LOCAL_ARCHIVES", value = "3" },
    { name = "REQUIRED_APP_VERSION", value = var.required_app_version },
    { name = "PLAY_INTEGRITY_PROJECT_NUMBER", value = var.play_integrity_project_number },
    { name = "PLAY_INTEGRITY_PACKAGE_NAME", value = "com.duducar.signage" },
    { name = "MEDIA_PROCESSING_LEASE_SECONDS", value = tostring(var.media_processing_lease_seconds) },
    { name = "MEDIA_DISPATCH_RETRY_SECONDS", value = tostring(var.media_dispatch_retry_seconds) },
    { name = "MEDIA_MAX_DISPATCH_ATTEMPTS", value = tostring(var.media_max_dispatch_attempts) },
    { name = "MEDIA_RECONCILE_MAX_ASSETS", value = tostring(var.media_reconcile_max_assets) },
    { name = "MEDIA_MAX_REQUEST_BYTES", value = "53477376" },
    { name = "MEDIA_MAX_IMAGE_PIXELS", value = "25000000" },
    { name = "MEDIA_MAX_IMAGE_DIMENSION", value = "10000" },
    { name = "MEDIA_CLAMAV_TIMEOUT_SECONDS", value = "120" },
    { name = "MEDIA_FFPROBE_TIMEOUT_SECONDS", value = "30" },
    { name = "MEDIA_FFMPEG_TIMEOUT_SECONDS", value = "300" },
    { name = "FRESHCLAM_TIMEOUT_SECONDS", value = "120" },
    { name = "MEDIA_WORKER_TIMEOUT_SECONDS", value = "1200" },
    { name = "PLAYBACK_BATCH_MAX_COMPRESSED_BYTES", value = "262144" },
    { name = "PLAYBACK_BATCH_MAX_DECOMPRESSED_BYTES", value = "1048576" },
    { name = "MEDIA_DISPATCH_MAX_CONCURRENT_TASKS", value = "2" },
    { name = "MEDIA_DISPATCH_MAX_TASKS_PER_HOUR", value = "6" },
    { name = "MEDIA_DISPATCH_STARTUP_GRACE_SECONDS", value = "120" },
    { name = "MEDIA_DISPATCH_AMBIGUITY_REUSE_SECONDS", value = "900" },
    { name = "MEDIA_DISPATCH_AWS_CONNECT_TIMEOUT_SECONDS", value = "5" },
    { name = "MEDIA_DISPATCH_AWS_READ_TIMEOUT_SECONDS", value = "10" },
    { name = "EMAIL_BACKEND", value = "django.core.mail.backends.smtp.EmailBackend" },
    { name = "EMAIL_HOST", value = var.smtp_host },
    { name = "EMAIL_PORT", value = tostring(var.smtp_port) },
    { name = "EMAIL_USE_TLS", value = "true" },
    { name = "EMAIL_USE_SSL", value = "false" },
    { name = "DEFAULT_FROM_EMAIL", value = var.default_from_email },
    { name = "SERVER_EMAIL", value = var.default_from_email },
    { name = "LOG_LEVEL", value = "INFO" }
  ]
  legacy_database_environment = var.enable_legacy_rds ? [
    { name = "DB_HOST", value = aws_db_instance.production[0].address },
    { name = "DB_PORT", value = "5432" },
    { name = "DB_NAME", value = "signage" },
    { name = "DB_USER", value = "signage" },
    { name = "DB_SSLMODE", value = "require" }
  ] : []
  common_environment = concat(local.shared_environment, local.legacy_database_environment)
  common_secrets = concat(
    [
      { name = "DJANGO_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.application.arn}:DJANGO_SECRET_KEY::" },
      { name = "EMAIL_HOST_USER", valueFrom = "${aws_secretsmanager_secret.application.arn}:EMAIL_HOST_USER::" },
      { name = "EMAIL_HOST_PASSWORD", valueFrom = "${aws_secretsmanager_secret.application.arn}:EMAIL_HOST_PASSWORD::" },
      { name = "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON", valueFrom = "${aws_secretsmanager_secret.application.arn}:PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON::" }
    ],
    var.enable_legacy_rds ? [
      { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.production[0].master_user_secret[0].secret_arn}:password::" }
    ] : []
  )
  task_secrets = concat(
    [
      { name = "DJANGO_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.application.arn}:DJANGO_SECRET_KEY::" }
    ],
    var.enable_legacy_rds ? [
      { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.production[0].master_user_secret[0].secret_arn}:password::" }
    ] : []
  )
}

check "service_image" {
  assert {
    condition     = !local.legacy_ecs_runtime_enabled || var.container_image != ""
    error_message = "The retired ECS runtime cannot be enabled without container_image. Current EC2 production requires enable_legacy_ecs_runtime=false; reviewed examples also keep enable_services=false."
  }
}

check "ec2_target_image" {
  assert {
    condition     = !var.enable_ec2_target || var.container_image != ""
    error_message = "container_image must be set before enable_ec2_target is true."
  }

  assert {
    condition     = !var.enable_ec2_target || var.enable_media_cloudfront
    error_message = "enable_media_cloudfront must be true whenever enable_ec2_target is true."
  }
}

check "app_update_configuration" {
  assert {
    condition = var.app_update_version_code == 0 ? (
      var.app_update_version_name == "" &&
      var.app_update_storage_name == "" &&
      var.app_update_sha256 == "" &&
      var.app_update_size_bytes == 0 &&
      var.app_update_rollout_percent == 0
      ) : (
      var.app_update_version_name != "" &&
      var.app_update_storage_name != "" &&
      var.app_update_sha256 != "" &&
      var.app_update_size_bytes > 0 &&
      var.app_update_rollout_percent > 0
    )
    error_message = "Configure every app-update field when OTA is enabled, or leave all staged APK fields at their zero/empty disabled values."
  }
}

check "application_origin" {
  assert {
    condition     = var.application_origin != "ec2" || var.enable_ec2_target
    error_message = "application_origin cannot switch to ec2 until enable_ec2_target is true."
  }

  assert {
    condition     = var.application_origin != "alb" || var.enable_legacy_alb
    error_message = "application_origin cannot select alb after the legacy ALB is disabled."
  }
}

check "legacy_ecs_runtime_dependencies" {
  assert {
    condition     = !local.legacy_ecs_runtime_enabled || (var.enable_legacy_alb && var.enable_legacy_rds)
    error_message = "The legacy ECS runtime requires both the legacy ALB and RDS."
  }
}

check "legacy_ecs_runtime_decommission" {
  assert {
    condition = var.enable_legacy_ecs_runtime || (
      var.application_origin == "ec2" &&
      var.enable_ec2_target &&
      var.ecs_web_desired_count == 0 &&
      !var.enable_ecs_schedules &&
      !var.enable_continuous_media_worker
    )
    error_message = "Switch production to the EC2 target and quiesce the legacy web service, schedules, and continuous worker before disabling the legacy ECS runtime."
  }
}

check "legacy_alb_decommission" {
  assert {
    condition = var.enable_legacy_alb || (
      !var.enable_legacy_ecs_runtime &&
      var.ecs_web_desired_count == 0 &&
      !var.enable_ecs_schedules &&
      !var.enable_continuous_media_worker &&
      var.application_origin == "ec2" &&
      var.enable_ec2_target &&
      !var.enable_ec2_acme_bridge
    )
    error_message = "Disable and quiesce the legacy ECS runtime, switch application_origin to ec2, and remove the ACME bridge before disabling the legacy ALB."
  }
}

check "legacy_rds_decommission" {
  assert {
    condition = !var.confirm_legacy_rds_final_snapshot || (
      !var.enable_legacy_rds &&
      trimspace(var.legacy_rds_final_snapshot_identifier) != ""
    )
    error_message = "Final-snapshot confirmation is valid only on the RDS removal plan and requires an explicit snapshot identifier."
  }

  assert {
    condition = var.enable_legacy_rds || (
      !var.enable_legacy_alb &&
      !var.enable_legacy_ecs_runtime &&
      var.application_origin == "ec2" &&
      !var.legacy_rds_deletion_protection &&
      var.confirm_legacy_rds_final_snapshot &&
      trimspace(var.legacy_rds_final_snapshot_identifier) != ""
    )
    error_message = "Legacy RDS deletion requires the ECS runtime and ALB to be disabled, deletion protection already disabled, and the explicit final snapshot separately confirmed."
  }
}

check "cloudfront_public_key" {
  assert {
    condition     = !var.enable_media_cloudfront || can(regex("-----BEGIN PUBLIC KEY-----", var.cloudfront_public_key_pem))
    error_message = "A PEM public key must be supplied before enable_media_cloudfront is true; keep its private key outside Terraform."
  }
}

check "migration_budget_notification_limit" {
  assert {
    condition     = length(var.migration_budget_forecast_thresholds) + length(var.migration_budget_actual_thresholds) <= 5
    error_message = "AWS Budgets permits at most five notifications per budget."
  }
}

resource "aws_vpc" "production" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "production" {
  vpc_id = aws_vpc.production.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.production.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(aws_vpc.production.cidr_block, 8, count.index)
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public-${count.index + 1}" }
}

resource "aws_subnet" "database" {
  count             = 2
  vpc_id            = aws_vpc.production.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(aws_vpc.production.cidr_block, 8, count.index + 10)
  tags              = { Name = "${local.name}-database-${count.index + 1}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.production.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.production.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  route_table_id = aws_route_table.public.id
  subnet_id      = aws_subnet.public[count.index].id
}

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public HTTPS load balancer"
  vpc_id      = aws_vpc.production.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "web" {
  name        = "${local.name}-web"
  description = "Web task ingress only from the ALB"
  vpc_id      = aws_vpc.production.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "Worker and scheduled tasks with no inbound access"
  vpc_id      = aws_vpc.production.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "database" {
  name        = "${local.name}-database"
  description = "PostgreSQL from ECS only"
  vpc_id      = aws_vpc.production.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.web.id, aws_security_group.tasks.id]
  }
}

resource "aws_kms_key" "production" {
  description             = "DUDU signage production data"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "production" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.production.key_id
}

resource "aws_s3_bucket" "media" {
  bucket = "${local.name}-media-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "backups" {
  bucket = "${local.name}-backups-${data.aws_caller_identity.current.account_id}"
}

data "aws_iam_policy_document" "backups_transport" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "backups_transport" {
  bucket = aws_s3_bucket.backups.id
  policy = data.aws_iam_policy_document.backups_transport.json
}

resource "aws_s3_bucket_public_access_block" "private" {
  for_each = {
    media   = aws_s3_bucket.media.id
    backups = aws_s3_bucket.backups.id
  }
  bucket                  = each.value
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "versioned" {
  for_each = {
    media   = aws_s3_bucket.media.id
    backups = aws_s3_bucket.backups.id
  }
  bucket = each.value
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "encrypted" {
  for_each = {
    media   = aws_s3_bucket.media.id
    backups = aws_s3_bucket.backups.id
  }
  bucket = each.value
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.production.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "retain-30-days"
    status = "Enabled"
    filter {}
    expiration { days = 30 }
    # Current-version expiration first makes the payload noncurrent. Remove it
    # on the next lifecycle pass instead of retaining it for another 30 days.
    noncurrent_version_expiration { noncurrent_days = 1 }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    id     = "remove-deleted-and-incomplete-media"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_db_subnet_group" "production" {
  name       = local.name
  subnet_ids = aws_subnet.database[*].id
}

resource "aws_db_instance" "production" {
  count                         = var.enable_legacy_rds ? 1 : 0
  identifier                    = local.name
  engine                        = "postgres"
  engine_version                = "16"
  instance_class                = "db.t4g.micro"
  allocated_storage             = 20
  max_allocated_storage         = 50
  storage_type                  = "gp3"
  storage_encrypted             = true
  kms_key_id                    = aws_kms_key.production.arn
  db_name                       = "signage"
  username                      = "signage"
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.production.key_id
  db_subnet_group_name          = aws_db_subnet_group.production.name
  vpc_security_group_ids        = [aws_security_group.database.id]
  publicly_accessible           = false
  multi_az                      = false
  backup_retention_period       = 30
  backup_window                 = "18:30-19:00"
  maintenance_window            = "sun:19:30-sun:20:30"
  auto_minor_version_upgrade    = true
  deletion_protection           = var.legacy_rds_deletion_protection
  skip_final_snapshot           = false
  final_snapshot_identifier     = local.legacy_rds_final_snapshot_identifier
  copy_tags_to_snapshot         = true
}

resource "aws_ecr_repository" "backend" {
  name                 = "duducar-signage-backend"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.production.arn
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire only untagged artifacts after fourteen days; retain every tagged rollback release"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_secretsmanager_secret" "application" {
  name                    = "${local.name}/application"
  kms_key_id              = aws_kms_key.production.arn
  recovery_window_in_days = 30
}

resource "aws_acm_certificate" "production" {
  domain_name               = var.dashboard_hostname
  subject_alternative_names = [var.api_hostname]
  validation_method         = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "certificate" {
  for_each = toset([var.dashboard_hostname, var.api_hostname])
  zone_id  = data.aws_route53_zone.primary.zone_id
  name = one([
    for option in aws_acm_certificate.production.domain_validation_options :
    option.resource_record_name if option.domain_name == each.value
  ])
  type = one([
    for option in aws_acm_certificate.production.domain_validation_options :
    option.resource_record_type if option.domain_name == each.value
  ])
  records = [one([
    for option in aws_acm_certificate.production.domain_validation_options :
    option.resource_record_value if option.domain_name == each.value
  ])]
  ttl = 300
}

resource "aws_acm_certificate_validation" "production" {
  certificate_arn         = aws_acm_certificate.production.arn
  validation_record_fqdns = [for record in aws_route53_record.certificate : record.fqdn]
}

resource "aws_lb" "production" {
  count                      = var.enable_legacy_alb ? 1 : 0
  name                       = "duducar-signage-prod"
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "web" {
  count       = var.enable_legacy_alb ? 1 : 0
  name        = "duducar-signage-web"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.production.id
  health_check {
    path                = "/health/ready/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_listener" "http" {
  count             = var.enable_legacy_alb ? 1 : 0
  load_balancer_arn = aws_lb.production[0].arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = var.enable_legacy_alb ? 1 : 0
  load_balancer_arn = aws_lb.production[0].arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.production.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web[0].arn
  }
}

resource "aws_route53_record" "application" {
  for_each = toset([var.dashboard_hostname, var.api_hostname])
  zone_id  = data.aws_route53_zone.primary.zone_id
  name     = each.value
  type     = "A"
  ttl      = var.application_origin == "ec2" ? 60 : null
  records  = var.application_origin == "ec2" ? compact([try(aws_eip.ec2_target[0].public_ip, "")]) : null

  dynamic "alias" {
    for_each = var.application_origin == "alb" ? [1] : []
    content {
      name                   = aws_lb.production[0].dns_name
      zone_id                = aws_lb.production[0].zone_id
      evaluate_target_health = true
    }
  }
}

resource "aws_ecs_cluster" "production" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = var.enable_container_insights ? "enabled" : "disabled"
  }
}

resource "aws_cloudwatch_log_group" "application" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

resource "aws_sns_topic" "operations" {
  name              = "${local.name}-operations"
  kms_master_key_id = aws_kms_key.production.arn

  depends_on = [aws_kms_key_policy.media_cloudfront]
}

resource "aws_sns_topic_subscription" "operations_email" {
  topic_arn = aws_sns_topic.operations.arn
  protocol  = "email"
  endpoint  = var.operations_email
}

data "aws_iam_policy_document" "operations_topic" {
  statement {
    sid       = "AllowCloudWatchAlarmNotifications"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.operations.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = local.ec2_alarm_arns
    }
  }
}

resource "aws_sns_topic_policy" "operations" {
  arn    = aws_sns_topic.operations.arn
  policy = data.aws_iam_policy_document.operations_topic.json
}

resource "aws_iam_role" "execution" {
  name = "${local.name}-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = concat(
          [aws_secretsmanager_secret.application.arn],
          var.enable_legacy_rds ? [aws_db_instance.production[0].master_user_secret[0].secret_arn] : []
        )
      },
      { Effect = "Allow", Action = ["kms:Decrypt"], Resource = [aws_kms_key.production.arn] }
    ]
  })
}

resource "aws_iam_role" "web_task" {
  name = "${local.name}-web-task"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "web_task" {
  role = aws_iam_role.web_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.media.arn] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.media.arn}/*"] },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"], Resource = [aws_kms_key.production.arn] },
      { Effect = "Allow", Action = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"], Resource = ["*"] }
    ]
  })
}

resource "aws_iam_role" "worker_task" {
  name = "${local.name}-worker-task"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "worker_task" {
  role = aws_iam_role.worker_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.media.arn] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.media.arn}/*"] },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"], Resource = [aws_kms_key.production.arn] }
    ]
  })
}

resource "aws_iam_role" "scheduled_task" {
  name = "${local.name}-scheduled-task"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "scheduled_task" {
  role = aws_iam_role.scheduled_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.media.arn] },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.backups.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["database-backups", "database-backups/*"]
          }
        }
      },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.media.arn}/*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = ["${aws_s3_bucket.backups.arn}/database-backups/*"] },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"], Resource = [aws_kms_key.production.arn] }
    ]
  })
}

locals {
  media_dispatch_environment = local.legacy_ecs_task_definitions_enabled ? [
    { name = "MEDIA_PROCESSING_DISPATCH_BACKEND", value = "ecs" },
    { name = "ECS_MEDIA_REGION", value = var.aws_region },
    { name = "ECS_MEDIA_CLUSTER", value = aws_ecs_cluster.production.arn },
    { name = "ECS_MEDIA_TASK_DEFINITION", value = aws_ecs_task_definition.worker[0].arn },
    { name = "ECS_MEDIA_CONTAINER_NAME", value = "application" },
    { name = "ECS_MEDIA_SUBNET_IDS", value = join(",", aws_subnet.public[*].id) },
    { name = "ECS_MEDIA_SECURITY_GROUP_IDS", value = aws_security_group.tasks.id },
    { name = "ECS_MEDIA_ASSIGN_PUBLIC_IP", value = "true" }
  ] : []

  web_delivery_environment = var.enable_media_cloudfront ? [
    { name = "AWS_S3_CUSTOM_DOMAIN", value = aws_cloudfront_distribution.media[0].domain_name },
    { name = "AWS_CLOUDFRONT_KEY_ID", value = aws_cloudfront_public_key.media[0].id }
  ] : []

  web_delivery_secrets = var.enable_media_cloudfront ? [
    { name = "AWS_CLOUDFRONT_PRIVATE_KEY", valueFrom = "${aws_secretsmanager_secret.application.arn}:AWS_CLOUDFRONT_PRIVATE_KEY::" }
  ] : []
}

resource "aws_ecs_task_definition" "application" {
  count                    = local.legacy_ecs_task_definitions_enabled ? 1 : 0
  family                   = local.name
  skip_destroy             = true
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.web_task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name         = "application"
    image        = var.container_image
    essential    = true
    portMappings = [{ containerPort = 8000, hostPort = 8000, protocol = "tcp" }]
    environment  = concat(local.common_environment, local.media_dispatch_environment, local.web_delivery_environment)
    secrets      = concat(local.common_secrets, local.web_delivery_secrets)
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/health/live/', headers={'Host': 'marketing.duducaradmin.com'}); urllib.request.urlopen(req, timeout=3)\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.application.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "web"
      }
    }
  }])

  tags = { Component = "web" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_ecs_task_definition" "worker" {
  count                    = local.legacy_ecs_task_definitions_enabled ? 1 : 0
  family                   = "${local.name}-worker"
  skip_destroy             = true
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name        = "application"
    image       = var.container_image
    essential   = true
    command     = ["sh", "worker-entrypoint.sh"]
    environment = local.common_environment
    secrets     = local.task_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.application.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  }])

  tags = { Component = "media-worker" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role_policy" "web_media_dispatch" {
  count = local.legacy_ecs_task_definitions_enabled ? 1 : 0
  role  = aws_iam_role.web_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [aws_ecs_task_definition.worker[0].arn] },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.worker_task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:TagResource"]
        Resource = ["arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.production.name}/*"]
        Condition = {
          StringEquals = { "ecs:CreateAction" = "RunTask" }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "scheduled_media_dispatch" {
  count = local.legacy_ecs_task_definitions_enabled ? 1 : 0
  role  = aws_iam_role.scheduled_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [aws_ecs_task_definition.worker[0].arn] },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.worker_task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:TagResource"]
        Resource = ["arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.production.name}/*"]
        Condition = {
          StringEquals = { "ecs:CreateAction" = "RunTask" }
        }
      }
    ]
  })
}

resource "aws_ecs_task_definition" "scheduled" {
  count                    = local.legacy_ecs_task_definitions_enabled ? 1 : 0
  family                   = "${local.name}-scheduled"
  skip_destroy             = true
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.scheduled_task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name        = "application"
    image       = var.container_image
    essential   = true
    command     = ["python", "manage.py", "check"]
    environment = concat(local.common_environment, local.media_dispatch_environment)
    secrets     = local.task_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.application.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "scheduled"
      }
    }
  }])

  tags = { Component = "scheduled" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_ecs_service" "web" {
  count                   = local.legacy_ecs_runtime_enabled && local.legacy_ecs_task_definitions_enabled && var.enable_legacy_alb ? 1 : 0
  name                    = "web"
  cluster                 = aws_ecs_cluster.production.id
  task_definition         = aws_ecs_task_definition.application[0].arn
  desired_count           = var.ecs_web_desired_count
  launch_type             = "FARGATE"
  enable_execute_command  = true
  enable_ecs_managed_tags = true
  propagate_tags          = "TASK_DEFINITION"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web[0].arn
    container_name   = "application"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.https[0]]
}

resource "aws_ecs_service" "worker" {
  count                   = local.legacy_ecs_runtime_enabled && local.legacy_ecs_task_definitions_enabled ? 1 : 0
  name                    = "media-worker"
  cluster                 = aws_ecs_cluster.production.id
  task_definition         = aws_ecs_task_definition.worker[0].arn
  desired_count           = var.enable_continuous_media_worker ? 1 : 0
  launch_type             = "FARGATE"
  enable_ecs_managed_tags = true
  propagate_tags          = "TASK_DEFINITION"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }
}

resource "aws_iam_role" "events" {
  name = "${local.name}-events"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "events" {
  count = local.legacy_ecs_task_definitions_enabled ? 1 : 0
  role  = aws_iam_role.events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [aws_ecs_task_definition.scheduled[0].arn] },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.execution.arn, aws_iam_role.scheduled_task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:TagResource"]
        Resource = ["arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.production.name}/*"]
        Condition = {
          StringEquals = { "ecs:CreateAction" = "RunTask" }
        }
      }
    ]
  })
}

locals {
  scheduled_tasks = local.legacy_ecs_runtime_enabled && local.legacy_ecs_task_definitions_enabled ? {
    fleet-health    = { expression = "rate(1 minute)", command = ["python", "manage.py", "evaluate_device_health"] },
    playlists       = { expression = "rate(6 hours)", command = ["python", "manage.py", "evaluate_playlists"] },
    media-reconcile = { expression = "rate(15 minutes)", command = ["python", "manage.py", "reconcile_media_processing"] },
    retention       = { expression = "cron(30 17 * * ? *)", command = ["python", "manage.py", "apply_retention"] },
    backup          = { expression = "cron(0 18 * * ? *)", command = ["python", "manage.py", "create_postgres_backup", "--output-dir", "/tmp/backups"] }
  } : {}
}

resource "aws_cloudwatch_event_rule" "scheduled" {
  for_each            = local.scheduled_tasks
  name                = "${local.name}-${each.key}"
  schedule_expression = each.value.expression
  state               = var.enable_ecs_schedules ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_target" "scheduled" {
  for_each  = local.scheduled_tasks
  rule      = aws_cloudwatch_event_rule.scheduled[each.key].name
  target_id = each.key
  arn       = aws_ecs_cluster.production.arn
  role_arn  = aws_iam_role.events.arn
  input = jsonencode({
    containerOverrides = [{ name = "application", command = each.value.command }]
  })
  ecs_target {
    task_definition_arn     = aws_ecs_task_definition.scheduled[0].arn
    launch_type             = "FARGATE"
    task_count              = 1
    enable_ecs_managed_tags = true
    propagate_tags          = "TASK_DEFINITION"
    network_configuration {
      subnets          = aws_subnet.public[*].id
      security_groups  = [aws_security_group.tasks.id]
      assign_public_ip = true
    }
  }
}

resource "aws_cloudwatch_event_rule" "ecs_task_failure" {
  name = "${local.name}-task-failure"
  event_pattern = jsonencode({
    source      = ["aws.ecs"]
    detail-type = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.production.arn]
      lastStatus = ["STOPPED"]
      containers = {
        exitCode = [{ anything-but = 0 }]
      }
    }
  })
}

resource "aws_iam_role" "eventbridge_sns" {
  name = "${local.name}-eventbridge-sns"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "events.amazonaws.com"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.ecs_task_failure.arn
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge_sns" {
  role = aws_iam_role.eventbridge_sns.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PublishOnlyOperationalAlerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [aws_sns_topic.operations.arn]
      },
      {
        Sid      = "UseOnlyOperationalAlertKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource = [aws_kms_key.production.arn]
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "ecs_task_failure" {
  rule      = aws_cloudwatch_event_rule.ecs_task_failure.name
  target_id = "operations"
  arn       = aws_sns_topic.operations.arn
  role_arn  = aws_iam_role.eventbridge_sns.arn

  depends_on = [aws_iam_role_policy.eventbridge_sns]
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  count               = var.enable_legacy_alb ? 1 : 0
  alarm_name          = "${local.name}-alb-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = { LoadBalancer = aws_lb.production[0].arn_suffix }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  count               = var.enable_legacy_alb ? 1 : 0
  alarm_name          = "${local.name}-unhealthy-targets"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions = {
    LoadBalancer = aws_lb.production[0].arn_suffix
    TargetGroup  = aws_lb_target_group.web[0].arn_suffix
  }
  alarm_actions = [aws_sns_topic.operations.arn]
  ok_actions    = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "database_storage" {
  count               = var.enable_legacy_rds ? 1 : 0
  alarm_name          = "${local.name}-database-low-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5368709120
  comparison_operator = "LessThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.production[0].id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  count               = var.enable_legacy_rds ? 1 : 0
  alarm_name          = "${local.name}-database-high-cpu"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.production[0].id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "database_connections" {
  count               = var.enable_legacy_rds ? 1 : 0
  alarm_name          = "${local.name}-database-high-connections"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 50
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.production[0].id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "scheduled_failures" {
  for_each            = local.scheduled_tasks
  alarm_name          = "${local.name}-${each.key}-schedule-failure"
  namespace           = "AWS/Events"
  metric_name         = "FailedInvocations"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = { RuleName = aws_cloudwatch_event_rule.scheduled[each.key].name }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  dynamic "notification" {
    for_each = toset([80, 90, 100])
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "FORECASTED"
      subscriber_email_addresses = [var.operations_email]
    }
  }
  dynamic "notification" {
    for_each = toset([90, 100])
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.operations_email]
    }
  }
}
