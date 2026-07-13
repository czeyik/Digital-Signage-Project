data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

data "aws_route53_zone" "primary" {
  name         = var.domain_name
  private_zone = false
}

locals {
  name = var.project_name
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
  common_environment = [
    { name = "DJANGO_DEBUG", value = "false" },
    { name = "DEPLOYMENT_ENV", value = "production" },
    { name = "DJANGO_ALLOWED_HOSTS", value = "${var.dashboard_hostname},${var.api_hostname}" },
    { name = "DJANGO_CSRF_TRUSTED_ORIGINS", value = "https://${var.dashboard_hostname}" },
    { name = "DJANGO_SECURE_SSL_REDIRECT", value = "true" },
    { name = "DJANGO_TRUST_X_FORWARDED_PROTO", value = "true" },
    { name = "DJANGO_USE_X_FORWARDED_HOST", value = "false" },
    { name = "DB_HOST", value = aws_db_instance.production.address },
    { name = "DB_PORT", value = "5432" },
    { name = "DB_NAME", value = "signage" },
    { name = "DB_USER", value = "signage" },
    { name = "DB_SSLMODE", value = "require" },
    { name = "AWS_STORAGE_BUCKET_NAME", value = aws_s3_bucket.media.bucket },
    { name = "AWS_S3_REGION_NAME", value = var.aws_region },
    { name = "PILOT_BACKUP_S3_BUCKET", value = aws_s3_bucket.backups.bucket },
    { name = "PILOT_BACKUP_RETENTION_DAYS", value = "30" },
    { name = "REQUIRED_APP_VERSION", value = var.required_app_version },
    { name = "PLAY_INTEGRITY_PROJECT_NUMBER", value = var.play_integrity_project_number },
    { name = "PLAY_INTEGRITY_PACKAGE_NAME", value = "com.duducar.signage" },
    { name = "EMAIL_BACKEND", value = "django.core.mail.backends.smtp.EmailBackend" },
    { name = "EMAIL_HOST", value = var.smtp_host },
    { name = "EMAIL_PORT", value = tostring(var.smtp_port) },
    { name = "EMAIL_USE_TLS", value = "true" },
    { name = "EMAIL_USE_SSL", value = "false" },
    { name = "DEFAULT_FROM_EMAIL", value = var.default_from_email },
    { name = "SERVER_EMAIL", value = var.default_from_email },
    { name = "LOG_LEVEL", value = "INFO" }
  ]
  common_secrets = [
    { name = "DJANGO_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.application.arn}:DJANGO_SECRET_KEY::" },
    { name = "EMAIL_HOST_USER", valueFrom = "${aws_secretsmanager_secret.application.arn}:EMAIL_HOST_USER::" },
    { name = "EMAIL_HOST_PASSWORD", valueFrom = "${aws_secretsmanager_secret.application.arn}:EMAIL_HOST_PASSWORD::" },
    { name = "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON", valueFrom = "${aws_secretsmanager_secret.application.arn}:PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON::" },
    { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.production.master_user_secret[0].secret_arn}:password::" }
  ]
  task_secrets = [
    { name = "DJANGO_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.application.arn}:DJANGO_SECRET_KEY::" },
    { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.production.master_user_secret[0].secret_arn}:password::" }
  ]
}

check "service_image" {
  assert {
    condition     = !var.enable_services || var.container_image != ""
    error_message = "container_image must be set before enable_services is true."
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
    noncurrent_version_expiration { noncurrent_days = 30 }
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
  deletion_protection           = true
  skip_final_snapshot           = false
  final_snapshot_identifier     = "${local.name}-final"
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
      description  = "Keep the latest 20 release images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
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
  for_each = {
    for option in aws_acm_certificate.production.domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }
  zone_id = data.aws_route53_zone.primary.zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 300
}

resource "aws_acm_certificate_validation" "production" {
  certificate_arn         = aws_acm_certificate.production.arn
  validation_record_fqdns = [for record in aws_route53_record.certificate : record.fqdn]
}

resource "aws_lb" "production" {
  name                       = "duducar-signage-prod"
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "web" {
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
  load_balancer_arn = aws_lb.production.arn
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
  load_balancer_arn = aws_lb.production.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.production.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_route53_record" "application" {
  for_each = toset([var.dashboard_hostname, var.api_hostname])
  zone_id  = data.aws_route53_zone.primary.zone_id
  name     = each.value
  type     = "A"
  alias {
    name                   = aws_lb.production.dns_name
    zone_id                = aws_lb.production.zone_id
    evaluate_target_health = true
  }
}

resource "aws_ecs_cluster" "production" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "application" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

resource "aws_sns_topic" "operations" {
  name              = "${local.name}-operations"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "operations_email" {
  topic_arn = aws_sns_topic.operations.arn
  protocol  = "email"
  endpoint  = var.operations_email
}

data "aws_iam_policy_document" "operations_topic" {
  statement {
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.operations.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
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
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [aws_secretsmanager_secret.application.arn, aws_db_instance.production.master_user_secret[0].secret_arn] },
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
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = [aws_s3_bucket.media.arn, aws_s3_bucket.backups.arn] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.media.arn}/*", "${aws_s3_bucket.backups.arn}/*"] },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"], Resource = [aws_kms_key.production.arn] }
    ]
  })
}

resource "aws_ecs_task_definition" "application" {
  count                    = var.container_image != "" ? 1 : 0
  family                   = local.name
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
    environment  = local.common_environment
    secrets      = local.common_secrets
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live/', timeout=3)\""]
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
}

resource "aws_ecs_task_definition" "worker" {
  count                    = var.container_image != "" ? 1 : 0
  family                   = "${local.name}-worker"
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
}

resource "aws_ecs_task_definition" "scheduled" {
  count                    = var.container_image != "" ? 1 : 0
  family                   = "${local.name}-scheduled"
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
    environment = local.common_environment
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
}

resource "aws_ecs_service" "web" {
  count                  = var.enable_services ? 1 : 0
  name                   = "web"
  cluster                = aws_ecs_cluster.production.id
  task_definition        = aws_ecs_task_definition.application[0].arn
  desired_count          = 1
  launch_type            = "FARGATE"
  enable_execute_command = true
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
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "application"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  count           = var.enable_services ? 1 : 0
  name            = "media-worker"
  cluster         = aws_ecs_cluster.production.id
  task_definition = aws_ecs_task_definition.worker[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"
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
  role = aws_iam_role.events.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = var.container_image != "" ? [aws_ecs_task_definition.scheduled[0].arn] : ["*"] },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.execution.arn, aws_iam_role.scheduled_task.arn] }
    ]
  })
}

locals {
  scheduled_tasks = var.enable_services ? {
    fleet-health = { expression = "rate(30 minutes)", command = ["python", "manage.py", "evaluate_device_health"] },
    playlists    = { expression = "rate(6 hours)", command = ["python", "manage.py", "evaluate_playlists"] },
    retention    = { expression = "cron(30 17 * * ? *)", command = ["python", "manage.py", "apply_retention"] },
    backup       = { expression = "cron(0 18 * * ? *)", command = ["python", "manage.py", "create_pilot_backup", "--output-dir", "/tmp/backups", "--skip-media"] }
  } : {}
}

resource "aws_cloudwatch_event_rule" "scheduled" {
  for_each            = local.scheduled_tasks
  name                = "${local.name}-${each.key}"
  schedule_expression = each.value.expression
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
    task_definition_arn = aws_ecs_task_definition.scheduled[0].arn
    launch_type         = "FARGATE"
    task_count          = 1
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

resource "aws_cloudwatch_event_target" "ecs_task_failure" {
  rule      = aws_cloudwatch_event_rule.ecs_task_failure.name
  target_id = "operations"
  arn       = aws_sns_topic.operations.arn
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name}-alb-5xx"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = { LoadBalancer = aws_lb.production.arn_suffix }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  alarm_name          = "${local.name}-unhealthy-targets"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions = {
    LoadBalancer = aws_lb.production.arn_suffix
    TargetGroup  = aws_lb_target_group.web.arn_suffix
  }
  alarm_actions = [aws_sns_topic.operations.arn]
  ok_actions    = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "database_storage" {
  alarm_name          = "${local.name}-database-low-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5368709120
  comparison_operator = "LessThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.production.id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name          = "${local.name}-database-high-cpu"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.production.id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "database_connections" {
  alarm_name          = "${local.name}-database-high-connections"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 50
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.production.id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "service_task_count" {
  for_each = var.enable_services ? {
    web    = aws_ecs_service.web[0].name
    worker = aws_ecs_service.worker[0].name
  } : {}
  alarm_name          = "${local.name}-${each.key}-task-count"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  dimensions = {
    ClusterName = aws_ecs_cluster.production.name
    ServiceName = each.value
  }
  alarm_actions = [aws_sns_topic.operations.arn]
  ok_actions    = [aws_sns_topic.operations.arn]
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
