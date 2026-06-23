#!/bin/bash
# Container-interface characterization for the fw2tar image.
#
# Proves the Nix-built image presents the SAME container interface the old
# Dockerfile did, in three areas the README/banner promise:
#
#   1. default command   - `docker run <img>` prints the install banner
#   2. command install    - `… fw2tar_install[.local]` emits an installer that
#                           drops the host wrapper, byte-for-byte equal to the
#                           repo's ./fw2tar (and ./fwstitch) sources
#   3. the fw2tar script  - `fakeroot_fw2tar <fw>` extracts a real image to a
#                           rootfs tarball; fw2tar/fwstitch are on PATH
#
# Self-contained: the host needs docker + nix.
#
# Env:
#   FW2TAR_IMAGE  image tag to test (default rehosting/fw2tar:latest)
#   NO_BUILD      if set, skip `nix build` (reuse an already-loaded image)
#   CONTAINER_WORK host scratch dir (default tests/container/.work)
#   KEEP_WORK     if set, keep the scratch dir
#
# Usage: ./run.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$TESTS_DIR/.." && pwd)"
IMAGE="${FW2TAR_IMAGE:-rehosting/fw2tar:latest}"
WORK="${CONTAINER_WORK:-$SCRIPT_DIR/.work}"

RED='\033[0;31m'; GREEN='\033[0;32m'; END='\033[0m'
pass=0; fail=0
ok()   { pass=$((pass+1)); echo -e "  ${GREEN}ok${END}   $1"; }
bad()  { fail=$((fail+1)); echo -e "  ${RED}FAIL${END} $1"; [ -n "${2:-}" ] && echo "       $2"; }

if [ -z "${NO_BUILD:-}" ]; then
    echo "== building production image via nix (.#dockerImage) =="
    ( cd "$REPO_DIR" && nix build .#dockerImage ) || {
        echo -e "${RED}failed to nix build .#dockerImage${END}"; exit 1; }
    IMAGE="$(docker load < "$REPO_DIR/result" | sed -n 's/^Loaded image: //p' | head -1)" || {
        echo -e "${RED}failed to load image${END}"; exit 1; }
    echo "loaded $IMAGE"
fi
echo "== testing image: $IMAGE =="

cleanup() { [ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"; }
trap cleanup EXIT
rm -rf "$WORK"; mkdir -p "$WORK"
UID_GID="$(id -u):$(id -g)"

# ---------------------------------------------------------------------------
echo "[1] default command (no args) prints the install banner"
# ---------------------------------------------------------------------------
banner="$(docker run --rm "$IMAGE" 2>/dev/null)"
for needle in "Welcome to the fw2tar container" \
              "docker run rehosting/fw2tar fw2tar_install" \
              "fw2tar --help"; do
    case "$banner" in
        *"$needle"*) ok "banner contains: $needle" ;;
        *)           bad "banner missing: $needle" ;;
    esac
done

# ---------------------------------------------------------------------------
echo "[2] entry points resolve on PATH and respond to --help"
# ---------------------------------------------------------------------------
for cmd in fw2tar fakeroot_fw2tar fwstitch banner.sh \
           fw2tar_install fw2tar_install.local \
           fwstitch_install fwstitch_install.local; do
    if docker run --rm --entrypoint sh "$IMAGE" -c "command -v $cmd" >/dev/null 2>&1; then
        ok "on PATH: $cmd"
    else
        bad "not on PATH: $cmd"
    fi
done
if docker run --rm "$IMAGE" fw2tar --help >/dev/null 2>&1; then
    ok "fw2tar --help exits 0"
else
    bad "fw2tar --help failed"
fi
if docker run --rm "$IMAGE" fakeroot_fw2tar --help >/dev/null 2>&1; then
    ok "fakeroot_fw2tar --help exits 0"
else
    bad "fakeroot_fw2tar --help failed"
fi

# ---------------------------------------------------------------------------
echo "[3] command install emits installers that match the repo wrappers"
# ---------------------------------------------------------------------------
# System-wide installers run via 'sudo sh' on the host; just assert the emitted
# script targets the right path and embeds the wrapper body verbatim.
sysfw="$(docker run --rm "$IMAGE" fw2tar_install 2>/dev/null)"
case "$sysfw" in
    *"tee /usr/local/bin/fw2tar"*) ok "fw2tar_install writes /usr/local/bin/fw2tar" ;;
    *) bad "fw2tar_install does not target /usr/local/bin/fw2tar" ;;
esac
# A distinctive line from the host wrapper must be embedded in the emitted script.
marker="$(grep -m1 -F 'image="rehosting/fw2tar"' "$REPO_DIR/fw2tar" || true)"
case "$sysfw" in
    *"$marker"*) ok "fw2tar_install embeds the host wrapper body" ;;
    *) bad "fw2tar_install does not embed the host wrapper" ;;
esac

# Local installers run via plain 'sh' (no sudo) — execute them inside the
# container with a throwaway HOME on the mounted work dir, then compare the
# installed wrapper to the repo source byte-for-byte.
run_local_install() {  # <installer> <relpath under HOME>
    rm -rf "$WORK/home"; mkdir -p "$WORK/home"
    docker run --rm -u "$UID_GID" -e HOME=/work/home -v "$WORK:/work" \
        --entrypoint sh "$IMAGE" -c "$1 | sh >/dev/null 2>&1; cat /work/home/$2" \
        2>/dev/null
}
if diff -q <(run_local_install fw2tar_install.local .local/bin/fw2tar) \
           "$REPO_DIR/fw2tar" >/dev/null 2>&1; then
    ok "fw2tar_install.local installs the exact repo ./fw2tar wrapper"
else
    bad "fw2tar_install.local wrapper differs from repo ./fw2tar"
fi
if diff -q <(run_local_install fwstitch_install.local .local/bin/fwstitch) \
           "$REPO_DIR/fwstitch" >/dev/null 2>&1; then
    ok "fwstitch_install.local installs the exact repo ./fwstitch wrapper"
else
    bad "fwstitch_install.local wrapper differs from repo ./fwstitch"
fi

# ---------------------------------------------------------------------------
echo "[4] running the fw2tar script extracts a real image to a rootfs tarball"
# ---------------------------------------------------------------------------
# Build the canonical synthetic rootfs (reusing the behavior harness's
# build_rootfs.py so it passes fw2tar's find_linux_filesystems heuristic),
# pack it into a squashfs (mksquashfs ships in the image), run the
# fakeroot_fw2tar entry point, and assert a non-empty *.rootfs.tar.gz that
# actually contains a known file is produced.
docker run --rm -u "$UID_GID" -v "$WORK:/work" -v "$TESTS_DIR:/tests:ro" \
    --entrypoint bash "$IMAGE" -c '
        set -e
        python3 /tests/behavior/build_rootfs.py /work/rfs /work/expected.json >/dev/null
        mksquashfs /work/rfs /work/fw.squashfs -all-root -noappend >/dev/null
        fakeroot_fw2tar /work/fw.squashfs >/dev/null 2>&1 || true
    ' >/dev/null 2>&1
tgz="$(ls "$WORK"/*.rootfs.tar.gz 2>/dev/null | head -1)"
if [ -n "$tgz" ] && [ -s "$tgz" ]; then
    if tar tzf "$tgz" 2>/dev/null | grep -q 'etc/passwd'; then
        ok "fakeroot_fw2tar produced $(basename "$tgz") (contains etc/passwd)"
    else
        bad "produced rootfs tarball missing etc/passwd" "$tgz"
    fi
else
    bad "fakeroot_fw2tar produced no *.rootfs.tar.gz"
fi

# ---------------------------------------------------------------------------
echo
echo "container interface: $pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
echo -e "${GREEN}container interface: OK${END}"
