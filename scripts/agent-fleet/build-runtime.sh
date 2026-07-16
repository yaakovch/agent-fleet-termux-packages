#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] || { echo "usage: $0 aarch64|x86_64 RELEASE_TAG" >&2; exit 2; }
architecture="$1"
release_tag="$2"
[[ "$architecture" == "aarch64" || "$architecture" == "x86_64" ]] || {
  echo "Agent Fleet supports only aarch64 and x86_64" >&2
  exit 2
}
[[ "$release_tag" =~ ^agent-fleet-runtime-[A-Za-z0-9._-]+$ ]] || {
  echo "invalid Agent Fleet runtime release tag" >&2
  exit 2
}

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
expected_package="com.yaakovch.fleet"
expected_prefix="/data/data/com.yaakovch.fleet/files/usr"

# shellcheck source=../properties.sh
. "$repo/scripts/properties.sh"
[[ "$TERMUX_APP__PACKAGE_NAME" == "$expected_package" && "$TERMUX__PREFIX" == "$expected_prefix" ]] || {
  echo "custom application ID or prefix is not active" >&2
  exit 1
}

artifact_root="$repo/output/agent-fleet-artifacts/$architecture"
bootstrap_root="$repo/output/agent-fleet-bootstraps"
rm -rf "$artifact_root"
mkdir -p "$artifact_root" "$bootstrap_root"
export TERMUX_BOOTSTRAP_OUTPUT_DIR="$bootstrap_root"

# build-bootstraps builds every dependency locally when the application ID is
# custom. The additional roots are the tools not already in its core bootstrap.
"$repo/scripts/build-bootstraps.sh" -f --architectures "$architecture" \
  --add ca-certificates,curl,git,openssh,python

python3 "$repo/scripts/agent-fleet/package-runtime.py" \
  --architecture "$architecture" \
  --release-tag "$release_tag" \
  --bootstrap "$bootstrap_root/bootstrap-$architecture.zip" \
  --packages "$repo/output" \
  --output "$artifact_root"
