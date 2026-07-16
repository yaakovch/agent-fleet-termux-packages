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
rm -rf "$artifact_root" "$bootstrap_root"
export TERMUX_BOOTSTRAP_OUTPUT_DIR="$bootstrap_root"

# Some bootstrap dependencies (notably termux-am) run their own Android Gradle
# build. The pinned package-builder image exposes an SDK that is intentionally
# read-only, so Gradle cannot auto-install its declared platform/build-tools.
# Install only those pinned components into an isolated writable SDK instead of
# mutating the builder image or depending on whatever it happens to contain.
source_sdk="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
[[ -n "$source_sdk" && -d "$source_sdk" ]] || {
  echo "the pinned builder did not expose an Android SDK" >&2
  exit 1
}
sdkmanager="$(find "$source_sdk" -type f -name sdkmanager -print -quit)"
[[ -x "$sdkmanager" ]] || {
  echo "the pinned builder Android SDK does not contain sdkmanager" >&2
  exit 1
}
writable_sdk="$HOME/.agent-fleet-android-sdk"
mkdir -p "$writable_sdk"
if [[ -d "$source_sdk/licenses" ]]; then
  rm -rf "$writable_sdk/licenses"
  cp -a "$source_sdk/licenses" "$writable_sdk/licenses"
fi
"$sdkmanager" --sdk_root="$writable_sdk" \
  "platforms;android-33" \
  "build-tools;30.0.3"
export ANDROID_HOME="$writable_sdk"
export ANDROID_SDK_ROOT="$writable_sdk"

# build-bootstraps builds every dependency locally when the application ID is
# custom. The additional roots are the tools not already in its core bootstrap.
"$repo/scripts/build-bootstraps.sh" -f --architectures "$architecture" \
  --add ca-certificates,libcurl,git,openssh,python

mkdir -p "$artifact_root"
python3 "$repo/scripts/agent-fleet/package-runtime.py" \
  --architecture "$architecture" \
  --release-tag "$release_tag" \
  --bootstrap "$bootstrap_root/bootstrap-$architecture.zip" \
  --packages "$repo/output" \
  --output "$artifact_root"
