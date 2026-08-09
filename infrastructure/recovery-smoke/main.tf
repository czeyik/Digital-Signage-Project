data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_subnet" "recovery" {
  id = var.recovery_subnet_id
}

data "aws_ebs_snapshot" "source" {
  most_recent = false
  owners      = ["self"]

  filter {
    name   = "snapshot-id"
    values = [var.source_snapshot_id]
  }

  filter {
    name   = "status"
    values = ["completed"]
  }
}

data "aws_ecr_image" "backend" {
  repository_name = local.backend_repository_name
  image_digest    = split("@", var.backend_image)[1]
}

locals {
  # These are constants rather than variables so this root cannot be retargeted
  # to another account, Region, or cost-allocation tag through a tfvars file.
  production_account_id = "173454940059"
  production_region     = "ap-southeast-5"
  project_tag_value     = "duducar-signage"

  name                       = "duducar-recovery-${var.operation_id}"
  recovery_hostname          = "recovery-${var.operation_id}.duducar.test"
  recovery_data_root         = "/var/lib/duducar-recovery/docker"
  recovery_runtime_dir       = "/run/duducar-recovery"
  recovery_state_bucket      = "duducar-signage-terraform-state-173454940059"
  recovery_state_key         = "recovery-smoke/${var.operation_id}.tfstate"
  recovery_state_region      = "ap-southeast-5"
  recovery_state_profile     = "dudu-production"
  backend_repository_name    = "duducar-signage-backend"
  backend_ecr_registry       = "${local.production_account_id}.dkr.ecr.${local.production_region}.amazonaws.com"
  backend_ecr_repository_arn = "arn:${data.aws_partition.current.partition}:ecr:${local.production_region}:${local.production_account_id}:repository/${local.backend_repository_name}"
  backup_bucket_arn          = "arn:${data.aws_partition.current.partition}:s3:::${var.backup_bucket_name}"
  media_bucket_arn           = "arn:${data.aws_partition.current.partition}:s3:::${var.media_bucket_name}"
  vpc_resolver_ip            = cidrhost(var.vpc_cidr, 2)
  backend_metadata           = try(jsondecode(file("${path.root}/.terraform/terraform.tfstate")), {})
  initialized_backend        = try(local.backend_metadata.backend, {})
  initialized_backend_config = try(local.initialized_backend.config, {})
  recovery_asset_separator   = "__DUDUCAR_RECOVERY_ASSET_SEPARATOR_V1__"
  recovery_asset_heredoc     = "__DUDUCAR_RECOVERY_ASSET_BUNDLE_EOF_V1__"
  recovery_asset_sources = [
    file("${path.module}/../terraform/ec2/runtime/pg_hba.conf"),
    file("${path.module}/../terraform/ec2/runtime/postgres-init-roles.sh"),
    file("${path.module}/../terraform/ec2/runtime/postgres-runtime-grants.sql"),
    file("${path.module}/runtime/Caddyfile.recovery"),
    file("${path.module}/runtime/duducar-recovery-mount"),
    file("${path.module}/runtime/render-recovery-runtime-env"),
    file("${path.module}/runtime/duducar-recovery-stack"),
    file("${path.module}/runtime/duducar-recovery-restore"),
  ]
  recovery_asset_bundle = join("\n${local.recovery_asset_separator}\n", local.recovery_asset_sources)
  recovery_user_data_base64 = base64gzip(templatefile("${path.module}/recovery-bootstrap.sh.tftpl", {
    aws_region_b64                    = base64encode(local.production_region)
    operation_id_b64                  = base64encode(var.operation_id)
    data_volume_id_b64                = base64encode(replace(aws_ebs_volume.recovery_data.id, "-", ""))
    backend_image_b64                 = base64encode(var.backend_image)
    postgres_image_b64                = base64encode(var.postgres_image)
    caddy_image_b64                   = base64encode(var.caddy_image)
    application_secret_arn_b64        = base64encode(var.application_secret_arn)
    media_bucket_name_b64             = base64encode(var.media_bucket_name)
    backup_bucket_name_b64            = base64encode(var.backup_bucket_name)
    recovery_hostname_b64             = base64encode(local.recovery_hostname)
    django_allowed_hosts_b64          = base64encode(local.recovery_hostname)
    django_csrf_trusted_origins_b64   = base64encode("https://${local.recovery_hostname}:${var.recovery_caddy_port}")
    required_app_version_b64          = base64encode(var.required_app_version)
    play_integrity_project_number_b64 = base64encode(var.play_integrity_project_number)
    cloudfront_domain_b64             = base64encode(var.cloudfront_domain)
    cloudfront_public_key_id_b64      = base64encode(var.cloudfront_public_key_id)
    recovery_caddy_port_b64           = base64encode(tostring(var.recovery_caddy_port))
    source_snapshot_id_b64            = base64encode(var.source_snapshot_id)
    source_data_volume_id_b64         = base64encode(var.source_data_volume_id)
    source_archive_key_b64            = base64encode(var.source_archive_key)
    source_archive_version_id_b64     = base64encode(var.source_archive_version_id)
    source_sidecar_key_b64            = base64encode(var.source_sidecar_key)
    source_sidecar_version_id_b64     = base64encode(var.source_sidecar_version_id)
    source_media_key_b64              = base64encode(var.source_media_key)
    source_media_version_id_b64       = base64encode(var.source_media_version_id)
    source_media_sha256_b64           = base64encode(var.source_media_sha256)
    source_media_size_bytes_b64       = base64encode(tostring(var.source_media_size_bytes))
    # The whole user-data script is gzip-compressed by base64gzip. Keep this
    # static source bundle raw inside that outer stream: nesting a base64-gzip
    # payload would make its bytes largely incompressible and exceed EC2's
    # 16 KiB raw user-data limit.
    recovery_assets_raw = local.recovery_asset_bundle
  }))
  recovery_user_data_raw_bytes = floor(length(local.recovery_user_data_base64) / 4) * 3 - (
    endswith(local.recovery_user_data_base64, "==") ? 2 :
    endswith(local.recovery_user_data_base64, "=") ? 1 : 0
  )
}

resource "terraform_data" "guardrails" {
  input = {
    account_id            = data.aws_caller_identity.current.account_id
    snapshot_id           = data.aws_ebs_snapshot.source.id
    source_volume_id      = data.aws_ebs_snapshot.source.volume_id
    recovery_subnet_vpc   = data.aws_subnet.recovery.vpc_id
    recovery_subnet_az    = data.aws_subnet.recovery.availability_zone
    backend_image_digest  = data.aws_ecr_image.backend.image_digest
    recovery_operation_id = var.operation_id
  }

  lifecycle {
    # The backend is outside normal Terraform variables. Check the initialized
    # default data-directory metadata and workspace as a second line of
    # defence; recovery-terraform is mandatory because TF_DATA_DIR is outside
    # Terraform module evaluation and it verifies these values before every
    # stateful command.
    precondition {
      condition = (
        try(local.initialized_backend.type, "") == "s3" &&
        try(local.initialized_backend_config.bucket, "") == local.recovery_state_bucket &&
        try(local.initialized_backend_config.key, "") == local.recovery_state_key &&
        try(local.initialized_backend_config.region, "") == local.recovery_state_region &&
        try(local.initialized_backend_config.profile, "") == local.recovery_state_profile &&
        try(local.initialized_backend_config.encrypt, false) == true &&
        try(local.initialized_backend_config.use_lockfile, false) == true
      )
      error_message = "Unsafe Terraform backend metadata. Run ./recovery-terraform init --operation-id ${var.operation_id}; the only permitted key is ${local.recovery_state_key}."
    }

    precondition {
      condition     = terraform.workspace == "default"
      error_message = "Recovery smoke must use the default Terraform workspace; recovery state never uses workspace prefixes."
    }

    precondition {
      condition = alltrue([for source in local.recovery_asset_sources : (
        !strcontains(source, local.recovery_asset_separator) &&
        !strcontains(source, local.recovery_asset_heredoc)
      )])
      error_message = "Recovery runtime asset source contains a reserved bundle separator or heredoc terminator."
    }

    precondition {
      condition     = data.aws_caller_identity.current.account_id == local.production_account_id
      error_message = "Recovery smoke is guarded for account ${local.production_account_id}; refusing another AWS account."
    }

    precondition {
      condition     = data.aws_subnet.recovery.vpc_id == var.recovery_vpc_id
      error_message = "recovery_subnet_id does not belong to recovery_vpc_id."
    }

    precondition {
      condition     = data.aws_ebs_snapshot.source.id == var.source_snapshot_id
      error_message = "The selected source_snapshot_id is not a completed self-owned snapshot."
    }

    precondition {
      condition     = data.aws_ebs_snapshot.source.volume_id == var.source_data_volume_id
      error_message = "The selected snapshot does not originate from source_data_volume_id."
    }

    precondition {
      condition     = data.aws_ebs_snapshot.source.encrypted
      error_message = "The selected source snapshot is not encrypted."
    }

    precondition {
      condition = (
        lookup(data.aws_ebs_snapshot.source.tags, "dlm:managed", "") == "true" &&
        lookup(data.aws_ebs_snapshot.source.tags, "DLMBackup", "") == "duducar-signage-production-ec2-target-data"
      )
      error_message = "The selected snapshot is not the scheduled DLM data-volume recovery point (required tags dlm:managed=true and DLMBackup=duducar-signage-production-ec2-target-data are missing)."
    }

    precondition {
      condition     = var.source_sidecar_key == "${var.source_archive_key}.sha256"
      error_message = "source_sidecar_key must be the SHA-256 sidecar for source_archive_key."
    }

    precondition {
      condition     = startswith(var.backend_image, "${local.backend_ecr_registry}/${local.backend_repository_name}@sha256:")
      error_message = "backend_image must be a digest in the exact production duducar-signage-backend ECR repository."
    }

    precondition {
      condition     = data.aws_ecr_image.backend.image_digest == split("@", var.backend_image)[1]
      error_message = "backend_image digest was not found in the exact production duducar-signage-backend ECR repository."
    }
  }
}

resource "aws_security_group" "recovery" {
  name        = local.name
  description = "Zero-ingress, SSM-only security group for isolated DUDU restore smoke ${var.operation_id}"
  vpc_id      = var.recovery_vpc_id

  # A public IPv4 address is used only for outbound HTTPS because this VPC has
  # no NAT or interface endpoints. Security groups are stateful; no ingress is
  # allowed, including 80, 443, SSH, PostgreSQL, or the recovery Caddy port.
  egress {
    description = "Outbound HTTPS only for SSM, AWS APIs, package repositories, and digest-pinned image pulls"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "UDP DNS only to the VPC resolver"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["${local.vpc_resolver_ip}/32"]
  }

  egress {
    description = "TCP DNS fallback only to the VPC resolver"
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["${local.vpc_resolver_ip}/32"]
  }

  depends_on = [terraform_data.guardrails]
}

data "aws_iam_policy_document" "recovery_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "recovery" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.recovery_assume_role.json
  description        = "Temporary least-privilege role for isolated restore smoke ${var.operation_id}"

  depends_on = [terraform_data.guardrails]
}

resource "aws_iam_role_policy_attachment" "recovery_ssm" {
  role       = aws_iam_role.recovery.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "recovery" {
  statement {
    sid       = "ReadOnlyApplicationSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.application_secret_arn]
  }

  statement {
    sid       = "DecryptApplicationSecretOnlyThroughSecretsManager"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.application_kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${local.production_region}.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:SecretARN"
      values   = [var.application_secret_arn]
    }
  }

  statement {
    sid       = "ReadOnlyExactArchiveVersion"
    effect    = "Allow"
    actions   = ["s3:GetObjectVersion"]
    resources = ["${local.backup_bucket_arn}/${var.source_archive_key}"]

    condition {
      test     = "StringEquals"
      variable = "s3:VersionId"
      values   = [var.source_archive_version_id]
    }
  }

  statement {
    sid       = "ReadOnlyExactArchiveSidecarVersion"
    effect    = "Allow"
    actions   = ["s3:GetObjectVersion"]
    resources = ["${local.backup_bucket_arn}/${var.source_sidecar_key}"]

    condition {
      test     = "StringEquals"
      variable = "s3:VersionId"
      values   = [var.source_sidecar_version_id]
    }
  }

  statement {
    sid       = "ReadOnlyExactNormalizedMediaVersion"
    effect    = "Allow"
    actions   = ["s3:GetObjectVersion"]
    resources = ["${local.media_bucket_arn}/${var.source_media_key}"]

    condition {
      test     = "StringEquals"
      variable = "s3:VersionId"
      values   = [var.source_media_version_id]
    }
  }

  statement {
    sid       = "DecryptOnlyS3ObjectsThroughS3"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.storage_kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${local.production_region}.amazonaws.com"]
    }

    condition {
      # S3 Bucket Keys bind their KMS encryption context to the bucket ARN,
      # not individual object or prefix ARNs. Object access remains separately
      # limited to the exact read-only objects above.
      test     = "StringEquals"
      variable = "kms:EncryptionContext:aws:s3:arn"
      values = [
        local.backup_bucket_arn,
        local.media_bucket_arn,
      ]
    }
  }

  statement {
    sid       = "AuthenticateToEcr"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullExactBackendRepositoryOnly"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [local.backend_ecr_repository_arn]
  }
}

resource "aws_iam_role_policy" "recovery" {
  name   = local.name
  role   = aws_iam_role.recovery.id
  policy = data.aws_iam_policy_document.recovery.json
}

resource "aws_iam_instance_profile" "recovery" {
  name = local.name
  role = aws_iam_role.recovery.name
}

resource "aws_ebs_volume" "recovery_data" {
  availability_zone = data.aws_subnet.recovery.availability_zone
  encrypted         = true
  kms_key_id        = var.data_volume_kms_key_arn
  snapshot_id       = var.source_snapshot_id
  size              = data.aws_ebs_snapshot.source.volume_size
  type              = "gp3"

  tags = {
    Name               = "${local.name}-data"
    SourceSnapshotId   = var.source_snapshot_id
    SourceDataVolumeId = var.source_data_volume_id
  }

  lifecycle {
    precondition {
      condition     = data.aws_ebs_snapshot.source.volume_size == 32
      error_message = "The selected recovery snapshot must be the expected 32 GiB production data volume."
    }
  }

  depends_on = [terraform_data.guardrails]
}

resource "aws_instance" "recovery" {
  ami                                  = var.recovery_ami_id
  instance_type                        = var.recovery_instance_type
  subnet_id                            = var.recovery_subnet_id
  vpc_security_group_ids               = [aws_security_group.recovery.id]
  associate_public_ip_address          = true
  iam_instance_profile                 = aws_iam_instance_profile.recovery.name
  monitoring                           = false
  disable_api_termination              = false
  instance_initiated_shutdown_behavior = "terminate"
  user_data_replace_on_change          = true

  root_block_device {
    delete_on_termination = true
    encrypted             = true
    kms_key_id            = var.data_volume_kms_key_arn
    volume_size           = var.recovery_root_volume_size_gib
    volume_type           = "gp3"

    tags = {
      Name = "${local.name}-root"
    }
  }

  credit_specification {
    cpu_credits = "standard"
  }

  # IMDSv2 remains available to the host AWS CLI, but a hop limit of one
  # prevents bridged Docker containers from obtaining the instance role.
  metadata_options {
    http_endpoint               = "enabled"
    http_protocol_ipv6          = "disabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  user_data_base64 = local.recovery_user_data_base64

  lifecycle {
    # AWS rejects raw EC2 user data over 16 KiB. The value is a base64-encoded
    # gzip stream, so derive its decoded byte length without attempting to
    # interpret compressed bytes as UTF-8. This is checked with the exact
    # operator inputs before an instance can be created.
    precondition {
      condition     = local.recovery_user_data_raw_bytes <= 16384
      error_message = "Recovery EC2 user data is ${local.recovery_user_data_raw_bytes} bytes; the EC2 raw user-data limit is 16384 bytes. Shorten reviewed non-secret inputs before apply."
    }
  }

  tags = {
    Name             = local.name
    SourceSnapshotId = var.source_snapshot_id
  }

  depends_on = [
    aws_iam_role_policy_attachment.recovery_ssm,
    aws_iam_role_policy.recovery,
  ]
}

resource "aws_volume_attachment" "recovery_data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.recovery_data.id
  instance_id = aws_instance.recovery.id

  # The recovery helper waits for this device but deliberately does not mount
  # it automatically. Operators inspect XFS read-only before a writable mount.
  skip_destroy = false
}
