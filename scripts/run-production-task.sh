#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 <container command> [arguments...]" >&2
    exit 2
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TF_DIR="$ROOT/infrastructure/terraform"
REGION=${AWS_REGION:-ap-southeast-5}

cluster=$(terraform -chdir="$TF_DIR" output -raw ecs_cluster)
task_definition=$(terraform -chdir="$TF_DIR" output -raw application_task_definition)
subnets=$(terraform -chdir="$TF_DIR" output -json public_subnet_ids | jq -r 'join(",")')
security_group=$(terraform -chdir="$TF_DIR" output -raw task_security_group_id)
command_json=$(jq -cn --args '$ARGS.positional' -- "$@")
overrides=$(jq -cn --argjson command "$command_json" '{containerOverrides:[{name:"application",command:$command}]}')
network="awsvpcConfiguration={subnets=[$subnets],securityGroups=[$security_group],assignPublicIp=ENABLED}"

task_arn=$(aws ecs run-task \
    --region "$REGION" \
    --cluster "$cluster" \
    --launch-type FARGATE \
    --task-definition "$task_definition" \
    --network-configuration "$network" \
    --overrides "$overrides" \
    --query 'tasks[0].taskArn' \
    --output text)

if [ -z "$task_arn" ] || [ "$task_arn" = "None" ]; then
    echo "ECS did not start the task." >&2
    exit 1
fi

echo "Started $task_arn"
aws ecs wait tasks-stopped --region "$REGION" --cluster "$cluster" --tasks "$task_arn"
exit_code=$(aws ecs describe-tasks \
    --region "$REGION" \
    --cluster "$cluster" \
    --tasks "$task_arn" \
    --query 'tasks[0].containers[0].exitCode' \
    --output text)
reason=$(aws ecs describe-tasks \
    --region "$REGION" \
    --cluster "$cluster" \
    --tasks "$task_arn" \
    --query 'tasks[0].stoppedReason' \
    --output text)
echo "Task exit code: $exit_code"
echo "Stopped reason: $reason"
test "$exit_code" = "0"
