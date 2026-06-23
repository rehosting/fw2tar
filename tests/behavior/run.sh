#!/bin/bash
# Behavior characterization harness for fw2tar (host entry point).
#
# Builds the Nix test image (fw2tar + extra filesystem builders, the .#testImage
# flake output) and runs the whole flow INSIDE it: synthesize one canonical
# rootfs, pack it into every filesystem type unblob handles, extract each with
# fw2tar, and assert the result against the documented expectations (see
# tests/BEHAVIOR.md).
#
# Self-contained: the host needs docker + nix. Fixture builders (mke2fs,
# mksquashfs, genromfs, genisoimage, ...) all live in the image, not on the host.
#
# Env:
#   TEST_IMAGE     test image tag (default rehosting/fw2tar-test:latest)
#   BEHAVIOR_WORK  host scratch/output dir (default tests/behavior/.work)
#   KEEP_WORK      if set, keep the scratch dir for inspection
#   NO_BUILD       if set, skip `nix build` (reuse an already-loaded TEST_IMAGE)
#
# Usage: ./run.sh [fixture ...]    (default: all)
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$TESTS_DIR/.." && pwd)"
TEST_IMAGE="${TEST_IMAGE:-rehosting/fw2tar-test:latest}"
WORK="${BEHAVIOR_WORK:-$SCRIPT_DIR/.work}"

RED='\033[0;31m'; GREEN='\033[0;32m'; END='\033[0m'

if [ -z "${NO_BUILD:-}" ]; then
    echo "== building test image via nix (.#testImage) =="
    ( cd "$REPO_DIR" && nix build .#testImage ) || {
        echo -e "${RED}failed to nix build .#testImage${END}"; exit 1; }
    TEST_IMAGE="$(docker load < "$REPO_DIR/result" | sed -n 's/^Loaded image: //p' | head -1)" || {
        echo -e "${RED}failed to load test image${END}"; exit 1; }
    echo "loaded $TEST_IMAGE"
fi

cleanup() { [ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"; }
trap cleanup EXIT
rm -rf "$WORK"; mkdir -p "$WORK"

# Run the driver inside the test image as the host user so output is host-owned.
docker run --rm \
    -u "$(id -u):$(id -g)" \
    -v "$TESTS_DIR:/tests:ro" \
    -v "$WORK:/work" \
    -e BEHAVIOR_WORK=/work \
    --entrypoint bash \
    "$TEST_IMAGE" /tests/behavior/run_in_container.sh "$@"
rc=$?

if [ "$rc" -eq 0 ]; then
    echo -e "${GREEN}behavior harness: OK${END}"
else
    echo -e "${RED}behavior harness: $rc fixture(s) failed (logs in $WORK if KEEP_WORK)${END}"
fi
exit "$rc"
