#!/bin/bash
# Output-layout contract test for fw2tar.
#
# Downstream consumers (penguin, scripted pipelines) depend on the EXACT shape
# of fw2tar's output directory, not just the tar contents — file names, where
# the winner lands, which per-candidate archives persist, and that nothing else
# (logs, extractor scaffolding, nested dirs) leaks. Extraction drift here has
# broken consumers before while content-diff tests stayed green, so this
# harness asserts the full directory listing byte-for-byte:
#
#   [A] basic run           <base>.rootfs.tar.gz (+ manifest sidecar) and one
#                           <base>.<extractor>.0.tar.gz per requested
#                           extractor; nothing else
#   [B] --primary-limit 2   secondaries land as <base>.<N>.rootfs.tar.gz with
#                           their own sidecars; the primary manifest (sidecar
#                           AND embedded gzip trailer) advertises them
#   [C] default naming      no --output: single trailing extension stripped,
#                           inner version dots preserved
#
# Fixtures are synthetic (behavior harness builders), so no network is needed.
# Requires the TEST image (fixture builders included).
#
# Env:
#   FW2TAR_IMAGE  image tag to test (default rehosting/fw2tar-test:latest)
#   NO_BUILD      if set, skip `nix build` (reuse an already-loaded image)
#   CONTRACT_WORK host scratch dir (default tests/contract/.work)
#   KEEP_WORK     if set, keep the scratch dir
#
# Usage: ./run.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$TESTS_DIR/.." && pwd)"
IMAGE="${FW2TAR_IMAGE:-rehosting/fw2tar-test:latest}"
WORK="${CONTRACT_WORK:-$SCRIPT_DIR/.work}"

RED='\033[0;31m'; GREEN='\033[0;32m'; END='\033[0m'
pass=0; fail=0
ok()   { pass=$((pass+1)); echo -e "  ${GREEN}ok${END}   $1"; }
bad()  { fail=$((fail+1)); echo -e "  ${RED}FAIL${END} $1"; [ -n "${2:-}" ] && echo "       $2"; }

if [ -z "${NO_BUILD:-}" ]; then
    echo "== building test image via nix (.#testImage) =="
    ( cd "$REPO_DIR" && nix build .#testImage ) || {
        echo -e "${RED}failed to nix build .#testImage${END}"; exit 1; }
    IMAGE="$(docker load < "$REPO_DIR/result" | sed -n 's/^Loaded image: //p' | head -1)" || {
        echo -e "${RED}failed to load image${END}"; exit 1; }
    echo "loaded $IMAGE"
fi
echo "== testing image: $IMAGE =="

cleanup() { [ -n "${KEEP_WORK:-}" ] || rm -rf "$WORK"; }
trap cleanup EXIT
rm -rf "$WORK"; mkdir -p "$WORK"
UID_GID="$(id -u):$(id -g)"

in_image() {  # run a bash snippet inside the test image with /work + /tests mounted
    docker run --rm -u "$UID_GID" -v "$WORK:/work" -v "$TESTS_DIR:/tests:ro" \
        --entrypoint bash "$IMAGE" -c "$1"
}

# Assert a directory's listing (relative paths, recursive) matches EXACTLY the
# expected newline-separated set. Any missing file, stray file, or unexpected
# nesting is a contract break.
assert_listing() {  # <label> <dir> <expected...>
    local label="$1" dir="$2"; shift 2
    local got want
    got="$(cd "$dir" && find . -mindepth 1 | sed 's|^\./||' | sort)"
    want="$(printf '%s\n' "$@" | sort)"
    if [ "$got" = "$want" ]; then
        ok "$label: output directory has exactly the contracted files"
    else
        bad "$label: output directory diverges from the contract" \
            "$(diff <(echo "$want") <(echo "$got") | sed 's/^/       /' | head -20)"
    fi
}

# Build the canonical synthetic rootfs + the fixtures once.
echo "[0] build synthetic fixtures (squashfs, two_squashfs_in_tar)"
if in_image '
        set -e
        python3 /tests/behavior/build_rootfs.py /work/rfs /work/expected.json >/dev/null
        /tests/behavior/make_images.sh /work/rfs /work/fixtures squashfs two_squashfs_in_tar >/dev/null
    '; then
    ok "fixtures built"
else
    bad "fixture build failed"; echo "cannot continue"; exit 1
fi

# ---------------------------------------------------------------------------
echo "[A] basic run: exact output layout for --extractors unblob,binwalk"
# ---------------------------------------------------------------------------
mkdir -p "$WORK/a/out"
cp "$WORK/fixtures/rootfs.squashfs" "$WORK/a/fw.squashfs"
if in_image '
        set -e
        fakeroot_fw2tar --extractors unblob,binwalk --output /work/a/out/fw /work/a/fw.squashfs
    ' > "$WORK/a/run.log" 2>&1; then
    ok "A: fw2tar exited 0"
else
    bad "A: fw2tar failed" "$(tail -5 "$WORK/a/run.log")"
fi
assert_listing "A" "$WORK/a/out" \
    "fw.rootfs.tar.gz" \
    "fw.rootfs.tar.gz.manifest.json" \
    "fw.unblob.0.tar.gz" \
    "fw.binwalk.0.tar.gz"
if python3 "$SCRIPT_DIR/check_contract.py" "$WORK/a/out/fw.rootfs.tar.gz" \
        --extractors unblob,binwalk --input "$WORK/a/fw.squashfs"; then
    ok "A: manifest contract holds (sidecar, trailer, input_hash)"
else
    bad "A: manifest contract violated"
fi

# ---------------------------------------------------------------------------
echo "[B] --primary-limit 2: secondaries land and are advertised"
# ---------------------------------------------------------------------------
mkdir -p "$WORK/b/out"
cp "$WORK/fixtures/rootfs.two_sqfs_in_tar" "$WORK/b/fw.bin"
if in_image '
        set -e
        fakeroot_fw2tar --extractors unblob --primary-limit 2 --output /work/b/out/fw /work/b/fw.bin
    ' > "$WORK/b/run.log" 2>&1; then
    ok "B: fw2tar exited 0"
else
    bad "B: fw2tar failed" "$(tail -5 "$WORK/b/run.log")"
fi
assert_listing "B" "$WORK/b/out" \
    "fw.rootfs.tar.gz" \
    "fw.rootfs.tar.gz.manifest.json" \
    "fw.1.rootfs.tar.gz" \
    "fw.1.rootfs.tar.gz.manifest.json" \
    "fw.unblob.0.tar.gz" \
    "fw.unblob.1.tar.gz"
if python3 "$SCRIPT_DIR/check_contract.py" "$WORK/b/out/fw.rootfs.tar.gz" \
        --extractors unblob --secondaries 1 --input "$WORK/b/fw.bin"; then
    ok "B: manifest contract holds (secondary advertised in sidecar + trailer)"
else
    bad "B: manifest contract violated"
fi
# The primary must be the larger tree (candidate ranking by file node count):
# rootfs_b drops usr/bin/prog9..11, so prog9 present == primary is rootfs_a.
if tar tzf "$WORK/b/out/fw.rootfs.tar.gz" 2>/dev/null | grep -q 'usr/bin/prog9'; then
    ok "B: primary is the larger candidate (ranking by file node count)"
else
    bad "B: primary is missing usr/bin/prog9 — candidate ranking regressed"
fi
if tar tzf "$WORK/b/out/fw.1.rootfs.tar.gz" 2>/dev/null | grep -q 'etc/passwd'; then
    ok "B: secondary archive is a real rootfs (contains etc/passwd)"
else
    bad "B: secondary archive missing etc/passwd"
fi

# ---------------------------------------------------------------------------
echo "[C] default naming: version dots preserved, single extension stripped"
# ---------------------------------------------------------------------------
mkdir -p "$WORK/c"
cp "$WORK/fixtures/rootfs.squashfs" "$WORK/c/fw-v1.2.3.bin"
if in_image '
        set -e
        fakeroot_fw2tar --extractors unblob /work/c/fw-v1.2.3.bin
    ' > "$WORK/c/run.log" 2>&1; then
    ok "C: fw2tar exited 0"
else
    bad "C: fw2tar failed" "$(tail -5 "$WORK/c/run.log")"
fi
assert_listing "C" "$WORK/c" \
    "run.log" \
    "fw-v1.2.3.bin" \
    "fw-v1.2.3.rootfs.tar.gz" \
    "fw-v1.2.3.rootfs.tar.gz.manifest.json" \
    "fw-v1.2.3.unblob.0.tar.gz"
if [ -e "$WORK/c/fw-v1.2.rootfs.tar.gz" ]; then
    bad "C: greedy extension strip regressed (fw-v1.2.rootfs.tar.gz exists)"
else
    ok "C: no greedily-stripped output name"
fi

# ---------------------------------------------------------------------------
echo
echo "output contract: $pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
echo -e "${GREEN}output contract: OK${END}"
