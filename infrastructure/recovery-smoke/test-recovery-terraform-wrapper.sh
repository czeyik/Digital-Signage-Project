#!/bin/bash
set -euo pipefail
umask 0077

# Exercise the wrapper's state-containment paths with a throwaway copy and a
# fake Terraform executable. No AWS command or real Terraform state is used.
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d /tmp/duducar-recovery-wrapper.XXXXXX)
test_root="$test_dir/root"
fake_bin="$test_dir/bin"
fake_log="$test_dir/terraform.log"
operation_id=0123456789abcdef0123456789abcdef

cleanup() {
  rm -rf -- "$test_dir"
}
trap cleanup EXIT

mkdir -p "$test_root/.terraform" "$fake_bin"
cp "$root_dir/recovery-terraform" "$test_root/recovery-terraform"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'printf "data=%s args=%s\\n" "${TF_DATA_DIR:-<unset>}" "$*" >> "$FAKE_TERRAFORM_LOG"' \
  'if [[ "$*" == *"workspace show"* ]]; then' \
  '  printf "%s\\n" default' \
  'fi' > "$fake_bin/terraform"
chmod 0755 "$fake_bin/terraform"

# The cleanup check must ask the native EC2 APIs whether this root's resources
# are still live. Resource Groups Tagging deliberately returns previously
# tagged/terminated resources, so make that API an unexpected invocation here.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'case "$1 $2" in' \
  '  "sts get-caller-identity") printf "%s\\n" 173454940059 ;;' \
  '  "ec2 describe-instances") printf "%s\\n" '\''{"Reservations":[{"Instances":[{"InstanceId":"i-stale","State":{"Name":"terminated"}}]}]} '\'' ;;' \
  '  "ec2 describe-volumes") printf "%s\\n" '\''{"Volumes":[]} '\'' ;;' \
  '  "ec2 describe-security-groups") printf "%s\\n" '\''{"SecurityGroups":[]} '\'' ;;' \
  '  "iam get-role"|"iam get-instance-profile") echo "NoSuchEntity" >&2; exit 254 ;;' \
  '  *) echo "Unexpected fake AWS invocation: $*" >&2; exit 1 ;;' \
  'esac' > "$fake_bin/aws"
chmod 0755 "$fake_bin/aws"

write_backend_metadata() {
  local key=$1
  printf '%s\n' \
    '{' \
    '  "backend": {' \
    '    "type": "s3",' \
    '    "config": {' \
    '      "bucket": "duducar-signage-terraform-state-173454940059",' \
    "      \"key\": \"$key\"," \
    '      "region": "ap-southeast-5",' \
    '      "profile": "dudu-production",' \
    '      "encrypt": true,' \
    '      "use_lockfile": true' \
    '    }' \
    '  }' \
    '}' > "$test_root/.terraform/terraform.tfstate"
}

run_wrapper() {
  PATH="$fake_bin:$PATH" \
    FAKE_TERRAFORM_LOG="$fake_log" \
    "$test_root/recovery-terraform" "$@"
}

# A production-key fixture must fail before Terraform runs, even if the caller
# attempts to redirect Terraform's data directory through the environment.
write_backend_metadata 'production/terraform.tfstate'
if output=$(TF_DATA_DIR="$test_dir/attacker-data" run_wrapper plan --operation-id "$operation_id" -input=false 2>&1); then
  echo "Wrapper accepted production backend metadata." >&2
  exit 1
else
  status=$?
fi
if [ "$status" -ne 1 ] || [[ "$output" != *'Recovery backend metadata is unsafe'* ]]; then
  echo "Wrapper did not fail closed for production backend metadata." >&2
  exit 1
fi
if [ -e "$fake_log" ]; then
  echo "Wrapper invoked Terraform before rejecting production backend metadata." >&2
  exit 1
fi

# With a safe metadata file, TF_DATA_DIR must still be cleared and the default
# workspace checked before a plan is delegated to Terraform.
write_backend_metadata "recovery-smoke/${operation_id}.tfstate"
TF_DATA_DIR="$test_dir/attacker-data" run_wrapper plan --operation-id "$operation_id" -input=false
if ! grep -Fqx 'data=<unset> args=-chdir='"$test_root"' workspace show' "$fake_log"; then
  echo "Wrapper did not clear TF_DATA_DIR before checking the workspace." >&2
  exit 1
fi

# An operator-supplied plan path must be refused after safe backend/workspace
# verification, before the wrapper can ask Terraform to apply anything.
before_apply_calls=$(wc -l < "$fake_log")
if output=$(run_wrapper apply --operation-id "$operation_id" /tmp/production.tfplan 2>&1); then
  echo "Wrapper accepted a positional saved plan for apply." >&2
  exit 1
else
  status=$?
fi
if [ "$status" -ne 2 ] || [[ "$output" != *'does not accept a positional saved-plan path'* ]]; then
  echo "Wrapper did not reject the positional saved plan as expected." >&2
  exit 1
fi
after_apply_calls=$(wc -l < "$fake_log")
if [ "$after_apply_calls" -ne $((before_apply_calls + 1)) ]; then
  echo "Wrapper delegated an unsafe apply instead of rejecting its saved plan." >&2
  exit 1
fi

# Apply must never accept Terraform's destroy mode under an APPLY confirmation.
# The wrapper owns that semantic boundary and provides a separate DESTROY flow.
before_destroy_flag_calls=$(wc -l < "$fake_log")
if output=$(run_wrapper apply --operation-id "$operation_id" -destroy 2>&1); then
  echo "Wrapper accepted -destroy through the apply path." >&2
  exit 1
else
  status=$?
fi
if [ "$status" -ne 2 ] || [[ "$output" != *'caller-supplied -destroy'* ]]; then
  echo "Wrapper did not reject apply -destroy as expected." >&2
  exit 1
fi
after_destroy_flag_calls=$(wc -l < "$fake_log")
if [ "$after_destroy_flag_calls" -ne $((before_destroy_flag_calls + 1)) ]; then
  echo "Wrapper delegated apply -destroy instead of rejecting it." >&2
  exit 1
fi

# Destroy uses the same verified-backend fresh-plan discipline, and its
# confirmation is bound to the exact operation instead of Terraform's generic
# `yes`. A wrong confirmation may produce the workspace/plan/show calls only.
before_destroy_calls=$(wc -l < "$fake_log")
if output=$(printf 'no\n' | run_wrapper destroy --operation-id "$operation_id" 2>&1); then
  echo "Wrapper accepted an unbound recovery destroy confirmation." >&2
  exit 1
else
  status=$?
fi
if [ "$status" -ne 1 ] || [[ "$output" != *'Recovery destroy was not confirmed'* ]]; then
  echo "Wrapper did not reject the unbound recovery destroy confirmation." >&2
  exit 1
fi
after_destroy_calls=$(wc -l < "$fake_log")
if [ "$after_destroy_calls" -ne $((before_destroy_calls + 3)) ]; then
  echo "Wrapper did not generate/show exactly one guarded destroy plan before rejecting confirmation." >&2
  exit 1
fi

# A terminated EC2 instance can remain in the tagging index after destroy. The
# native resource APIs are authoritative: a stale terminated record and no
# volume/security group/IAM objects must pass cleanup-check without real AWS.
run_wrapper cleanup-check --operation-id "$operation_id"

echo "Recovery Terraform wrapper containment checks passed."
