#!/bin/bash
set -euo pipefail
umask 0077

terraform_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
main="$terraform_dir/main.tf"
ec2_target="$terraform_dir/ec2_target.tf"

require() {
  rg -F --quiet "$1" "$2" || {
    echo "Missing required SNS alert KMS policy: $1" >&2
    exit 1
  }
}

require 'operations_sns_topic_arn = "arn:${data.aws_partition.current.partition}:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.name}-operations"' "$main"
require 'Sid      = "UseOperationsSnsKey"' "$ec2_target"
require 'Action   = ["kms:Decrypt", "kms:GenerateDataKey*"]' "$ec2_target"
require 'Resource = [aws_kms_key.production.arn]' "$ec2_target"
require '"kms:ViaService"                         = "sns.${var.aws_region}.amazonaws.com"' "$ec2_target"
require 'Sid    = "AllowOperationsSnsEncryption"' "$ec2_target"
require 'Service = "sns.amazonaws.com"' "$ec2_target"
require '"aws:SourceAccount"                      = data.aws_caller_identity.current.account_id' "$ec2_target"
require '"kms:EncryptionContext:aws:sns:topicArn" = local.operations_sns_topic_arn' "$ec2_target"

context_count=$(rg -F '"kms:EncryptionContext:aws:sns:topicArn" = local.operations_sns_topic_arn' "$ec2_target" | wc -l)
[ "$context_count" -ge 2 ] || {
  echo "The EC2 publisher and SNS service must both be scoped to the Operations topic." >&2
  exit 1
}

echo "Encrypted Operations SNS publisher KMS policy check passed."
