#!/usr/bin/env bash
# Verify Docker itself omits local credentials while retaining application input.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
scratch_dir=$(mktemp -d)
context_dir="$scratch_dir/context"
output_dir="$scratch_dir/output"

readonly required_files=(
  manage.py
  pyproject.toml
  config/settings.py
  signage/models.py
  signage/nested/keep.py
  signage/nested/manifest.json
  templates/base.html
  worker-entrypoint.sh
)

readonly excluded_files=(
  .env
  .env.production
  .envrc
  runtime.env
  runtime.env.production
  local.secret
  local.secrets
  local.credentials
  local.token
  local.tokens
  private.key
  private.pem
  signing.p8
  signing.p12
  signing.pfx
  signing.jks
  signing.keystore
  signing.gpg
  signing.pgp
  id_rsa
  id_ecdsa
  id_ed25519
  id_dsa
  id_xmss
  id_ecdsa_sk
  id_ed25519_sk
  signing.properties
  signing.properties.local
  keystore.properties
  keystore.properties.local
  secrets/runtime.json
  credentials/local.json
  private/application.key
  signing/release.keystore
  .aws/config
  .aws/credentials
  .ssh/config
  .ssh/id_rsa
  .docker/config.json
  .config/gcloud/credentials.db
  play-integrity.json
  application-secret.json
  django-secret-key
  database-password
  email-host-user
  email-host-password
  service-account.json
  service-account-production.json
  .netrc
  .pypirc
  pip.conf
  .git-credentials
)

nested_excluded_files=()
for excluded_file in "${excluded_files[@]}"; do
  nested_excluded_files+=("nested/$excluded_file")
done
readonly nested_excluded_files

all_excluded_files=("${excluded_files[@]}" "${nested_excluded_files[@]}")
readonly all_excluded_files

cleanup() {
  rm -rf -- "$scratch_dir"
}
trap cleanup EXIT

mkdir -p \
  "$context_dir/config" \
  "$context_dir/signage" \
  "$context_dir/templates" \
  "$context_dir/secrets" \
  "$context_dir/credentials" \
  "$context_dir/private" \
  "$context_dir/signing"

cp "$repo_root/backend/.dockerignore" "$context_dir/.dockerignore"

for required_file in "${required_files[@]}"; do
  install -Dm 0644 /dev/null "$context_dir/$required_file"
done

for excluded_file in "${all_excluded_files[@]}"; do
  install -Dm 0600 /dev/null "$context_dir/$excluded_file"
done

printf '%s\n' 'FROM scratch' 'COPY . /context/' \
  | docker buildx build \
      --progress=quiet \
      --output "type=local,dest=$output_dir" \
      --file - \
      "$context_dir"

for required_file in "${required_files[@]}"; do
  test -f "$output_dir/context/$required_file" || {
    echo "Required application input was excluded: $required_file" >&2
    exit 1
  }
done

for excluded_file in "${all_excluded_files[@]}"; do
  test ! -e "$output_dir/context/$excluded_file" || {
    echo "Local secret material entered the Docker build context: $excluded_file" >&2
    exit 1
  }
done
