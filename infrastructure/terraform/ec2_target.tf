# Migration-era Terraform addresses, AWS descriptions, alarm names, and tags in
# this file are intentionally stable to avoid replacing the live host or its
# security groups. Despite the ec2_target/candidate/replacement vocabulary,
# these resources now implement current USD 30 production.
data "aws_partition" "current" {}

data "aws_ssm_parameter" "al2023_arm64" {
  count = var.enable_ec2_target ? 1 : 0
  name  = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "ec2_target" {
  count                  = var.enable_ec2_target ? 1 : 0
  name                   = "${local.name}-ec2-target"
  description            = "Phase-one web ingress and PostgreSQL from isolated media workers"
  vpc_id                 = aws_vpc.production.id
  revoke_rules_on_delete = true

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-ec2-target" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group" "ec2_target_worker" {
  count       = var.enable_ec2_target ? 1 : 0
  name        = "${local.name}-ec2-media-worker"
  description = "Dedicated network identity for isolated candidate media tasks"
  vpc_id      = aws_vpc.production.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-ec2-media-worker" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "ec2_target_web" {
  for_each = var.enable_ec2_target ? {
    http  = 80
    https = 443
  } : {}

  security_group_id = aws_security_group.ec2_target[0].id
  description       = "Public ${each.key}"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = each.value
  to_port           = each.value
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "ec2_target_postgresql" {
  count = var.enable_ec2_target ? 1 : 0

  security_group_id            = aws_security_group.ec2_target[0].id
  description                  = "PostgreSQL only from isolated ECS media tasks"
  referenced_security_group_id = aws_security_group.ec2_target_worker[0].id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_network_interface" "ec2_target" {
  count           = var.enable_ec2_target ? 1 : 0
  subnet_id       = aws_subnet.public[0].id
  security_groups = [aws_security_group.ec2_target[0].id]

  tags = {
    Name = "${local.name}-ec2-target"
    Role = "replacement-web-database"
  }
}

resource "aws_eip" "ec2_target" {
  count             = var.enable_ec2_target ? 1 : 0
  domain            = "vpc"
  network_interface = aws_network_interface.ec2_target[0].id

  tags = { Name = "${local.name}-ec2-target" }
}

resource "aws_ebs_volume" "ec2_target_data" {
  count             = var.enable_ec2_target ? 1 : 0
  availability_zone = aws_subnet.public[0].availability_zone
  encrypted         = true
  size              = 32
  type              = "gp3"

  tags = {
    Name      = "${local.name}-ec2-target-data"
    DLMBackup = "${local.name}-ec2-target-data"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role" "ec2_target" {
  count = var.enable_ec2_target ? 1 : 0
  name  = "${local.name}-ec2-target"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_target_ssm" {
  count      = var.enable_ec2_target ? 1 : 0
  role       = aws_iam_role.ec2_target[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "ec2_target" {
  count = var.enable_ec2_target ? 1 : 0
  role  = aws_iam_role.ec2_target[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadRuntimeSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.application.arn]
      },
      {
        Sid      = "UseApplicationKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.production.arn]
      },
      {
        Sid      = "LocatePrivateBuckets"
        Effect   = "Allow"
        Action   = ["s3:GetBucketLocation"]
        Resource = [aws_s3_bucket.media.arn, aws_s3_bucket.backups.arn]
      },
      {
        Sid      = "ListMediaPrefixes"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.media.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["quarantine", "quarantine/*", "validated", "validated/*"]
          }
        }
      },
      {
        Sid      = "ListDatabaseBackups"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.backups.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["database-backups", "database-backups/*"]
          }
        }
      },
      {
        Sid    = "UseMediaObjects"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "${aws_s3_bucket.media.arn}/quarantine/*",
          "${aws_s3_bucket.media.arn}/validated/*"
        ]
      },
      {
        Sid      = "UseDatabaseBackups"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.backups.arn}/database-backups/*"]
      },
      {
        Sid      = "AuthenticateToEcr"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = ["*"]
      },
      {
        Sid    = "PullBackendImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer"
        ]
        Resource = [aws_ecr_repository.backend.arn]
      },
      {
        Sid      = "PublishOperationalAlerts"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [aws_sns_topic.operations.arn]
      }
    ]
  })
}

resource "aws_iam_role_policy" "ec2_target_media_dispatch" {
  count = var.enable_ec2_target && var.container_image != "" ? 1 : 0
  role  = aws_iam_role.ec2_target[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunIsolatedMediaTask"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = [aws_ecs_task_definition.ec2_target_worker[0].arn]
      },
      {
        Sid      = "InspectIsolatedMediaTaskCapacity"
        Effect   = "Allow"
        Action   = ["ecs:ListTasks"]
        Resource = ["*"]
      },
      {
        Sid      = "PassIsolatedMediaTaskRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.ec2_target_worker_execution[0].arn, aws_iam_role.ec2_target_worker[0].arn]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        Sid      = "TagIsolatedMediaTask"
        Effect   = "Allow"
        Action   = ["ecs:TagResource"]
        Resource = ["arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.production.name}/*"]
        Condition = {
          StringEquals = {
            "ecs:CreateAction" = "RunTask"
          }
        }
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_target" {
  count = var.enable_ec2_target ? 1 : 0
  name  = "${local.name}-ec2-target"
  role  = aws_iam_role.ec2_target[0].name
}

resource "aws_instance" "ec2_target" {
  count                       = var.enable_ec2_target ? 1 : 0
  ami                         = data.aws_ssm_parameter.al2023_arm64[0].value
  instance_type               = var.ec2_target_instance_type
  iam_instance_profile        = aws_iam_instance_profile.ec2_target[0].name
  monitoring                  = false
  disable_api_termination     = var.ec2_target_termination_protection
  user_data_replace_on_change = false

  primary_network_interface {
    network_interface_id = aws_network_interface.ec2_target[0].id
  }

  root_block_device {
    delete_on_termination = true
    encrypted             = true
    volume_size           = 8
    volume_type           = "gp3"

    tags = {
      Name = "${local.name}-ec2-target-root"
    }
  }

  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_protocol_ipv6          = "disabled"
    http_put_response_hop_limit = 2
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  # EC2 accepts at most 16 KiB of decoded user data. Cloud-init transparently
  # expands gzip payloads, keeping the complete, auditable bootstrap reproducible.
  user_data_base64 = base64gzip(templatefile("${path.module}/ec2/bootstrap.sh.tftpl", {
    aws_region                   = var.aws_region
    application_secret_arn       = aws_secretsmanager_secret.application.arn
    media_bucket                 = aws_s3_bucket.media.bucket
    backup_bucket                = aws_s3_bucket.backups.bucket
    dashboard_hostname           = var.dashboard_hostname
    api_hostname                 = var.api_hostname
    required_app_version         = var.required_app_version
    play_integrity_project       = var.play_integrity_project_number
    smtp_host                    = var.smtp_host
    smtp_port                    = var.smtp_port
    default_from_email           = var.default_from_email
    operations_sns_topic         = aws_sns_topic.operations.arn
    data_volume_id               = replace(aws_ebs_volume.ec2_target_data[0].id, "-", "")
    ecs_cluster                  = aws_ecs_cluster.production.arn
    ecs_worker_task_definition   = "${local.name}-ec2-media-worker"
    ecs_media_subnet_ids         = join(",", aws_subnet.public[*].id)
    ecs_media_security_group_ids = aws_security_group.ec2_target_worker[0].id
    cloudfront_domain            = try(aws_cloudfront_distribution.media[0].domain_name, "")
    cloudfront_public_key_id     = try(aws_cloudfront_public_key.media[0].id, "")
    media_processing_lease       = var.media_processing_lease_seconds
    media_dispatch_retry         = var.media_dispatch_retry_seconds
    media_max_dispatch_attempts  = var.media_max_dispatch_attempts
    media_reconcile_max_assets   = var.media_reconcile_max_assets
    caddyfile_preflight_b64      = filebase64("${path.module}/ec2/runtime/Caddyfile.preflight")
    caddyfile_production_b64     = filebase64("${path.module}/ec2/runtime/Caddyfile.production")
    caddyfile_post_cutover_b64   = filebase64("${path.module}/ec2/runtime/Caddyfile.post-cutover")
    postgres_hba_b64             = filebase64("${path.module}/ec2/runtime/pg_hba.conf")
    postgres_init_roles_b64      = filebase64("${path.module}/ec2/runtime/postgres-init-roles.sh")
    postgres_runtime_grants_b64  = filebase64("${path.module}/ec2/runtime/postgres-runtime-grants.sql")
    render_env_b64               = filebase64("${path.module}/ec2/runtime/render-runtime-env")
    stack_b64                    = filebase64("${path.module}/ec2/runtime/duducar-stack")
    command_b64                  = filebase64("${path.module}/ec2/runtime/duducar-command")
    alert_b64                    = filebase64("${path.module}/ec2/runtime/duducar-alert")
    host_health_b64              = filebase64("${path.module}/ec2/runtime/duducar-host-health")
    service_b64                  = filebase64("${path.module}/ec2/runtime/duducar.service")
    command_service_b64          = filebase64("${path.module}/ec2/runtime/duducar-command@.service")
    alert_service_b64            = filebase64("${path.module}/ec2/runtime/duducar-alert@.service")
    health_timer_b64             = filebase64("${path.module}/ec2/runtime/duducar-health.timer")
    playlist_timer_b64           = filebase64("${path.module}/ec2/runtime/duducar-playlists.timer")
    reconcile_timer_b64          = filebase64("${path.module}/ec2/runtime/duducar-media-reconcile.timer")
    retention_timer_b64          = filebase64("${path.module}/ec2/runtime/duducar-retention.timer")
    backup_timer_b64             = filebase64("${path.module}/ec2/runtime/duducar-backup.timer")
  }))

  tags = {
    Name = "${local.name}-ec2-target"
    Role = "replacement-web-database"
  }

  lifecycle {
    # User data is bootstrap-only. Post-provisioning runtime updates are
    # deployed explicitly through SSM so a Terraform apply cannot restart the
    # production host merely because the checked-in bootstrap evolved.
    ignore_changes = [ami, user_data_base64]
  }

  depends_on = [
    aws_eip.ec2_target,
    aws_iam_role_policy_attachment.ec2_target_ssm,
    aws_iam_role_policy.ec2_target,
    aws_iam_role_policy.ec2_target_media_dispatch,
    aws_iam_role_policy_attachment.dlm_data_volume,
    aws_route_table_association.public,
  ]
}

resource "aws_volume_attachment" "ec2_target_data" {
  count       = var.enable_ec2_target ? 1 : 0
  device_name = "/dev/sdf"
  instance_id = aws_instance.ec2_target[0].id
  volume_id   = aws_ebs_volume.ec2_target_data[0].id
}

resource "aws_lb_target_group" "ec2_target_acme" {
  # Retired migration bridge retained only as a false-by-default historical
  # control. Current production has no ALB and keeps this resource absent.
  count       = var.enable_ec2_target && var.enable_ec2_acme_bridge && var.enable_legacy_alb ? 1 : 0
  name        = "duducar-prod-ec2-acme"
  port        = 80
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.production.id

  health_check {
    enabled             = true
    path                = "/health/live/"
    port                = "traffic-port"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }

  tags = {
    Name      = "${local.name}-ec2-acme-bridge"
    Temporary = "remove-after-dns-cutover"
  }
}

resource "aws_lb_target_group_attachment" "ec2_target_acme" {
  count            = var.enable_ec2_target && var.enable_ec2_acme_bridge && var.enable_legacy_alb ? 1 : 0
  target_group_arn = aws_lb_target_group.ec2_target_acme[0].arn
  target_id        = aws_network_interface.ec2_target[0].private_ip
  port             = 80
}

resource "aws_lb_listener_rule" "ec2_target_acme" {
  count        = var.enable_ec2_target && var.enable_ec2_acme_bridge && var.enable_legacy_alb ? 1 : 0
  listener_arn = aws_lb_listener.http[0].arn
  priority     = 1

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ec2_target_acme[0].arn
  }

  condition {
    host_header {
      values = [var.dashboard_hostname, var.api_hostname]
    }
  }

  condition {
    path_pattern {
      values = ["/.well-known/acme-challenge/*"]
    }
  }

  tags = {
    Name      = "${local.name}-ec2-acme-bridge"
    Temporary = "remove-after-dns-cutover"
  }
}

resource "aws_iam_role" "ec2_target_worker_execution" {
  count = var.enable_ec2_target && var.container_image != "" ? 1 : 0
  name  = "${local.name}-ec2-worker-execution"

  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_target_worker_execution" {
  count      = var.enable_ec2_target && var.container_image != "" ? 1 : 0
  role       = aws_iam_role.ec2_target_worker_execution[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ec2_target_worker_execution" {
  count = var.enable_ec2_target && var.container_image != "" ? 1 : 0
  role  = aws_iam_role.ec2_target_worker_execution[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.application.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [aws_kms_key.production.arn]
      }
    ]
  })
}

resource "aws_iam_role" "ec2_target_worker" {
  count = var.enable_ec2_target && var.container_image != "" ? 1 : 0
  name  = "${local.name}-ec2-worker"

  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "ec2_target_worker" {
  count = var.enable_ec2_target && var.container_image != "" ? 1 : 0
  role  = aws_iam_role.ec2_target_worker[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:ListBucket"], Resource = [aws_s3_bucket.media.arn] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.media.arn}/*"] },
      { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"], Resource = [aws_kms_key.production.arn] }
    ]
  })
}

locals {
  ec2_target_worker_environment = var.enable_ec2_target && var.container_image != "" ? concat(
    local.shared_environment,
    [
      { name = "DB_HOST", value = aws_network_interface.ec2_target[0].private_ip },
      { name = "DB_PORT", value = "5432" },
      { name = "DB_NAME", value = "signage" },
      { name = "DB_USER", value = "signage_app" },
      { name = "DB_SSLMODE", value = "require" }
    ]
  ) : []
}

resource "aws_ecs_task_definition" "ec2_target_worker" {
  count                    = var.enable_ec2_target && var.container_image != "" ? 1 : 0
  family                   = "${local.name}-ec2-media-worker"
  skip_destroy             = true
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.ec2_target_worker_execution[0].arn
  task_role_arn            = aws_iam_role.ec2_target_worker[0].arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name        = "application"
    image       = var.container_image
    essential   = true
    command     = ["sh", "worker-entrypoint.sh"]
    environment = local.ec2_target_worker_environment
    secrets = [
      { name = "DJANGO_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.application.arn}:DJANGO_SECRET_KEY::" },
      { name = "DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.application.arn}:DB_PASSWORD::" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.application.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ec2-media-worker"
      }
    }
  }])

  tags = { Component = "ec2-media-worker" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_iam_role" "dlm_data_volume" {
  count = var.enable_ec2_target ? 1 : 0
  name  = "${local.name}-data-snapshots"

  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "dlm.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "dlm_data_volume" {
  count      = var.enable_ec2_target ? 1 : 0
  role       = aws_iam_role.dlm_data_volume[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "ec2_target_data" {
  count              = var.enable_ec2_target ? 1 : 0
  description        = "Daily encrypted snapshot of the DUDU EC2 data volume"
  execution_role_arn = aws_iam_role.dlm_data_volume[0].arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags = {
      DLMBackup = "${local.name}-ec2-target-data"
    }

    schedule {
      name      = "Daily data-volume snapshots"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["18:30"]
      }

      retain_rule {
        count = 30
      }
    }
  }

  tags = { Name = "${local.name}-ec2-target-data" }

  depends_on = [aws_iam_role_policy_attachment.dlm_data_volume]
}

resource "aws_cloudwatch_metric_alarm" "ec2_target_status" {
  count               = var.enable_ec2_target ? 1 : 0
  alarm_name          = "${local.name}-ec2-target-status"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"
  dimensions          = { InstanceId = aws_instance.ec2_target[0].id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "ec2_target_cpu" {
  count               = var.enable_ec2_target ? 1 : 0
  alarm_name          = "${local.name}-ec2-target-high-cpu"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 80
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"
  dimensions          = { InstanceId = aws_instance.ec2_target[0].id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_metric_alarm" "ec2_target_cpu_credits" {
  count               = var.enable_ec2_target ? 1 : 0
  alarm_name          = "${local.name}-ec2-target-low-cpu-credits"
  namespace           = "AWS/EC2"
  metric_name         = "CPUCreditBalance"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = 20
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"
  dimensions          = { InstanceId = aws_instance.ec2_target[0].id }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudfront_public_key" "media" {
  count       = var.enable_media_cloudfront ? 1 : 0
  name        = "${local.name}-media"
  comment     = "DUDU validated-media signed URL public key"
  encoded_key = var.cloudfront_public_key_pem
}

resource "aws_cloudfront_key_group" "media" {
  count   = var.enable_media_cloudfront ? 1 : 0
  name    = "${local.name}-media"
  comment = "Trusted signing keys for private validated media"
  items   = [aws_cloudfront_public_key.media[0].id]
}

resource "aws_cloudfront_origin_access_control" "media" {
  count                             = var.enable_media_cloudfront ? 1 : 0
  name                              = "${local.name}-media"
  description                       = "SigV4 access to the private media bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_cache_policy" "validated_media" {
  count       = var.enable_media_cloudfront ? 1 : 0
  name        = "${local.name}-validated-media"
  comment     = "Immutable validated media; signed URL fields are handled by CloudFront"
  default_ttl = 86400
  max_ttl     = 2592000
  min_ttl     = 300

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_distribution" "media" {
  count               = var.enable_media_cloudfront ? 1 : 0
  enabled             = true
  comment             = "Private delivery of validated DUDU media"
  default_root_object = ""
  http_version        = "http2and3"
  is_ipv6_enabled     = true
  price_class         = "PriceClass_All"
  retain_on_delete    = true
  wait_for_deployment = true

  origin {
    domain_name              = aws_s3_bucket.media.bucket_regional_domain_name
    origin_id                = "private-media-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.media[0].id
  }

  default_cache_behavior {
    target_origin_id       = "private-media-s3"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = aws_cloudfront_cache_policy.validated_media[0].id
    compress               = true
    viewer_protocol_policy = "redirect-to-https"
    trusted_key_groups     = [aws_cloudfront_key_group.media[0].id]
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    # CloudFront's default *.cloudfront.net certificate only supports this
    # policy value. A hard TLS 1.2 floor requires a custom alias and ACM
    # certificate in us-east-1; keeping an impossible value causes perpetual
    # drift while providing no enforcement.
    minimum_protocol_version = "TLSv1"
  }

  tags = { Name = "${local.name}-validated-media" }
}

data "aws_iam_policy_document" "media_cloudfront" {
  count = 1

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.media.arn, "${aws_s3_bucket.media.arn}/*"]

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

  dynamic "statement" {
    for_each = var.enable_media_cloudfront ? [1] : []

    content {
      sid       = "AllowCloudFrontValidatedMedia"
      effect    = "Allow"
      actions   = ["s3:GetObject"]
      resources = ["${aws_s3_bucket.media.arn}/validated/*"]

      principals {
        type        = "Service"
        identifiers = ["cloudfront.amazonaws.com"]
      }

      condition {
        test     = "StringEquals"
        variable = "AWS:SourceArn"
        values   = [aws_cloudfront_distribution.media[0].arn]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "media_cloudfront" {
  count  = 1
  bucket = aws_s3_bucket.media.id
  policy = data.aws_iam_policy_document.media_cloudfront[0].json
}

resource "aws_kms_key_policy" "media_cloudfront" {
  count  = 1
  key_id = aws_kms_key.production.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid    = "EnableAccountIamPolicies"
          Effect = "Allow"
          Principal = {
            AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"
          }
          Action   = "kms:*"
          Resource = "*"
        },
        {
          Sid    = "AllowProjectCloudWatchAlarmNotifications"
          Effect = "Allow"
          Principal = {
            Service = "cloudwatch.amazonaws.com"
          }
          Action   = ["kms:Decrypt", "kms:GenerateDataKey*"]
          Resource = "*"
          Condition = {
            StringEquals = {
              "aws:SourceAccount" = data.aws_caller_identity.current.account_id
            }
            ArnEquals = {
              "aws:SourceArn" = local.ec2_alarm_arns
            }
          }
        }
      ],
      var.enable_media_cloudfront ? [
        {
          Sid    = "AllowCloudFrontValidatedMedia"
          Effect = "Allow"
          Principal = {
            Service = "cloudfront.amazonaws.com"
          }
          Action   = ["kms:Decrypt", "kms:DescribeKey"]
          Resource = "*"
          Condition = {
            StringEquals = {
              "AWS:SourceArn" = aws_cloudfront_distribution.media[0].arn
            }
          }
        }
      ] : []
    )
  })
}

resource "aws_budgets_budget" "migration_target" {
  count        = var.enable_ec2_target ? 1 : 0
  name         = "${local.name}-migration-target"
  budget_type  = "COST"
  limit_amount = tostring(var.migration_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = [format("user:Project$%s", var.budget_project_tag_value)]
  }

  dynamic "notification" {
    for_each = var.migration_budget_forecast_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "FORECASTED"
      subscriber_email_addresses = [var.operations_email]
    }
  }

  dynamic "notification" {
    for_each = var.migration_budget_actual_thresholds
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.operations_email]
    }
  }
}
