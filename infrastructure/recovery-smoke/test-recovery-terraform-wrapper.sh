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

echo "Recovery Terraform wrapper containment checks passed."
