# Agent Fleet Android runtime

This fork builds the fixed internal runtime for the Android application ID
`com.yaakovch.fleet`. Packages are compiled for the prefix
`/data/data/com.yaakovch.fleet/files/usr` and cannot be mixed with packages
built for official Termux.

The public release assets are supply-chain inputs, not a user-facing APT
repository. Agent Fleet does not expose `pkg install` or a general local shell.
Each release contains an arm64 or x86_64 ZIP with a custom bootstrap, the full
locally built Debian package closure, a hash lock, and an SPDX SBOM.

To reproduce one architecture inside the pinned Termux builder container:

```bash
TERMUX_BUILDER_IMAGE_NAME=ghcr.io/termux/package-builder@sha256:fa23eb4238ef8eda877cd991a06152ce76e9f274d1cae0d42f28fee3e5cd6016 \
  ./scripts/run-docker.sh ./scripts/agent-fleet/build-runtime.sh aarch64 agent-fleet-runtime-c7ca367
```

The same command accepts `x86_64`. GitHub Actions builds both architectures
independently and publishes only after both jobs pass.
