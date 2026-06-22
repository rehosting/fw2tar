#!/bin/bash
# Behavior characterization harness for fw2tar (host entry point).
#
# Builds a test image (fw2tar + extra filesystem builders, see Dockerfile) and
# runs the whole flow INSIDE it: synthesize one canonical rootfs, pack it into
# every filesystem type unblob handles, extract each with fw2tar, and assert the
# result against the documented expectations (see tests/BEHAVIOR.md).
#
# Self-contained: the host only needs docker. Fixture builders (mke2fs, mksquashfs,
# genromfs, genisoimage, ...) all live in the image, not on the host.
#
# Env:
#   FW2TAR_IMAGE   base image to extend (default rehosting/fw2tar:latest)
#   TEST_IMAGE     tag for the built test image (default fw2tar-behavior:latest)
#   BEHAVIOR_WORK  host scratch/output dir (default tests/behavior/.work)
#   KEEP_WORK      if set, keep the scratch dir for inspection
#   NO_BUILD       if set, skip docker build (reuse existing TEST_IMAGE)
#
# Usage: ./run.sh [fixture ...]    (default: all)
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FW2TAR_IMAGE="${FW2TAR_IMAGE:-rehosting/fw2tar:latest}"
TEST_IMAGE="${TEST_IMAGE:-fw2tar-behavior:latest}"
WORK="${BEHAVIOR_WORK:-$SCRIPT_DIR/.work}"

RED='\033[0;31m'; GREEN='\033[0;32m'; END='\033[0m'

if [ -z "${NO_BUILD:-}" ]; then
    echo "== building test image $TEST_IMAGE (base $FW2TAR_IMAGE) =="
    docker build -q -t "$TEST_IMAGE" \
        --build-arg "BASE_IMAGE=$FW2TAR_IMAGE" \
        -f "$SCRIPT_DIR/Dockerfile" "$SCRIPT_DIR" >/dev/null || {
        echo -e "${RED}failed to build test image${END}"; exit 1; }
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
