mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = {
      names = ["ap-southeast-5a", "ap-southeast-5b"]
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "173454940059"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_data "aws_route53_zone" {
    defaults = {
      zone_id = "ZCURRENTTOPOLOGY"
    }
  }

  mock_data "aws_ssm_parameter" {
    defaults = {
      value = "ami-0123456789abcdef0"
    }
  }

  mock_resource "aws_acm_certificate" {
    defaults = {
      domain_validation_options = [
        {
          domain_name           = "marketing.duducaradmin.com"
          resource_record_name  = "_test.marketing.duducaradmin.com"
          resource_record_type  = "CNAME"
          resource_record_value = "validation.example.test"
        }
      ]
    }
  }
}

run "current_production_topology" {
  command = plan

  override_resource {
    target = aws_acm_certificate.production
    values = {
      domain_validation_options = [
        {
          domain_name           = "marketing.duducaradmin.com"
          resource_record_name  = "_test.marketing.duducaradmin.com"
          resource_record_type  = "CNAME"
          resource_record_value = "validation.example.test"
        }
      ]
    }
  }

  override_resource {
    target = aws_ecr_repository.backend
    values = {
      arn            = "arn:aws:ecr:ap-southeast-5:173454940059:repository/duducar-signage-production-backend"
      name           = "duducar-signage-production-backend"
      repository_url = "173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-production-backend"
    }
  }

  override_resource {
    target          = aws_dlm_lifecycle_policy.ec2_target_data
    override_during = plan
    values = {
      id = "policy-0123456789abcdef0"
    }
  }

  variables {
    operations_email                     = "operations@example.test"
    container_image                      = "173454940059.dkr.ecr.ap-southeast-5.amazonaws.com/duducar-signage-backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    postgres_image                       = "postgres@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    caddy_image                          = "caddy@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    cloudfront_public_key_pem            = "-----BEGIN PUBLIC KEY-----\ntest-only\n-----END PUBLIC KEY-----"
    legacy_rds_final_snapshot_identifier = "duducar-signage-production-final-reviewed"
    confirm_legacy_rds_final_snapshot    = true
  }

  assert {
    condition = (
      length(aws_db_instance.production) == 0 &&
      length(aws_lb.production) == 0 &&
      length(aws_ecs_service.web) == 0 &&
      length(aws_ecs_service.worker) == 0 &&
      length(aws_cloudwatch_event_rule.scheduled) == 0
    )
    error_message = "The current production fixture must not recreate retired RDS, ALB, ECS services, or application schedules."
  }

  assert {
    condition = (
      length(aws_instance.ec2_target) == 1 &&
      length(aws_cloudfront_distribution.media) == 1 &&
      var.application_origin == "ec2"
    )
    error_message = "The current production fixture must retain the EC2 host, private CloudFront delivery, and EC2 DNS origin."
  }

  assert {
    condition     = aws_instance.ec2_target[0].metadata_options[0].http_put_response_hop_limit == 1
    error_message = "The host must retain IMDS hop-limit one."
  }

  assert {
    condition = (
      length(aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_create_failed) == 1 &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_create_failed[0].namespace == "AWS/EBS" &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_create_failed[0].metric_name == "SnapshotsCreateFailed" &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_create_failed[0].dimensions["DLMPolicyId"] == "policy-0123456789abcdef0" &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_create_failed[0].comparison_operator == "GreaterThanThreshold" &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_create_failed[0].threshold == 0 &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_create_failed[0].treat_missing_data == "notBreaching"
    )
    error_message = "The current DLM policy must alarm independently when snapshot creation exhausts its retries."
  }

  assert {
    condition = (
      length(aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale) == 1 &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].evaluation_periods == 36 &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].datapoints_to_alarm == 36 &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].comparison_operator == "LessThanThreshold" &&
      aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].threshold == 1 &&
      one([for query in aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].metric_query : query if query.id == "completed"]).metric[0].namespace == "AWS/EBS" &&
      one([for query in aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].metric_query : query if query.id == "completed"]).metric[0].metric_name == "SnapshotsCreateCompleted" &&
      one([for query in aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].metric_query : query if query.id == "completed"]).metric[0].dimensions["DLMPolicyId"] == "policy-0123456789abcdef0" &&
      one([for query in aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].metric_query : query if query.id == "completed"]).metric[0].period == 3600 &&
      one([for query in aws_cloudwatch_metric_alarm.ec2_target_dlm_snapshot_stale[0].metric_query : query if query.id == "fresh"]).expression == "FILL(completed, 0)"
    )
    error_message = "The DLM freshness alarm must convert the sparse daily completion metric into 36 explicit hourly checks."
  }

  assert {
    condition = (
      aws_ebs_volume.ec2_target_data[0].tags["DLMBackup"] ==
      aws_dlm_lifecycle_policy.ec2_target_data[0].policy_details[0].target_tags["DLMBackup"] &&
      length(local.ec2_alarm_arns) == 5 &&
      contains(local.ec2_alarm_arns, "arn:aws:cloudwatch:ap-southeast-5:173454940059:alarm:duducar-signage-production-dlm-snapshot-create-failed") &&
      contains(local.ec2_alarm_arns, "arn:aws:cloudwatch:ap-southeast-5:173454940059:alarm:duducar-signage-production-dlm-snapshot-stale")
    )
    error_message = "The protected volume, DLM target, SNS topic policy, and KMS alarm allowlist must stay aligned."
  }

  assert {
    condition = (
      length(aws_ssm_document.ec2_runtime_assets[0].content) <= 65536 &&
      length(aws_ssm_document.ec2_release_config[0].content) <= 65536 &&
      length(aws_ssm_document.ec2_release_activation[0].content) <= 65536
    )
    error_message = "Audited runtime, configuration, and activation documents must remain within the SSM 64-KiB document limit."
  }

}
