#!/bin/bash
# In-container driver for the behavior harness. Runs INSIDE the fw2tar test image
# (the .#testImage flake output), where both the fixture builders and the fw2tar
# binary live.
#
# Builds the canonical rootfs, packs it into every supported filesystem image, then
# extracts each image with EVERY extractor (unblob, binwalk, binwalkv3) and records
# the per-cell outcome. Produces a fixture x extractor matrix and gates on a table
# of expected outcomes (see tests/BEHAVIOR.md).
#
#   /tests  -> repo tests/ dir (read-only bind mount)
#   /work   -> scratch + output (bind mount)
set -u

BEHAVIOR="/tests/behavior"
WORK="${BEHAVIOR_WORK:-/work}"
export PYTHONDONTWRITEBYTECODE=1

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; END='\033[0m'

EXTRACTORS=(unblob binwalk binwalkv3)

# fixture name | image file
FIXTURES=(
    "squashfs|rootfs.squashfs"
    "cramfs|rootfs.cramfs"
    "ubifs|rootfs.ubifs"
    "jffs2|rootfs.jffs2"
    "ext2|rootfs.ext2"
    "ext3|rootfs.ext3"
    "ext4|rootfs.ext4"
    "romfs|rootfs.romfs"
    "yaffs|rootfs.yaffs2"
    "iso9660|rootfs.iso"
    "fat|rootfs.fat"
    "cpio|rootfs.cpio"
    "tar|rootfs.tar"
    "zip|rootfs.zip"
)

# Expected outcome category per "<fixture>/<extractor>" cell:
#   ok    rootfs produced, every entry matches expectations (full fidelity)
#   diff  rootfs produced but some metadata lost (a known bug)
#   none  no rootfs produced (extractor can't / metadata lost so it isn't detected)
#   skip  fixture image could not be built (e.g. yaffs without mkyaffs2image)
# Cells with no entry are reported but NOT gated (yaffs is left ungated because its
# image only builds when mkyaffs2image is present). Captured 2026-06-22 against the
# Docker image; re-baselined for the Nix image (binwalk v3.1.0 from nixpkgs now
# extracts cramfs/cpio where the older build produced no rootfs).
declare -A EXPECT=(
    [squashfs/unblob]=ok    [squashfs/binwalk]=ok    [squashfs/binwalkv3]=ok
    [cramfs/unblob]=ok      [cramfs/binwalk]=ok      [cramfs/binwalkv3]=diff
    [ubifs/unblob]=ok       [ubifs/binwalk]=ok       [ubifs/binwalkv3]=none
    [jffs2/unblob]=diff     [jffs2/binwalk]=diff     [jffs2/binwalkv3]=diff
    [ext2/unblob]=diff      [ext2/binwalk]=none      [ext2/binwalkv3]=none
    [ext3/unblob]=diff      [ext3/binwalk]=none      [ext3/binwalkv3]=none
    [ext4/unblob]=diff      [ext4/binwalk]=none      [ext4/binwalkv3]=none
    [romfs/unblob]=none     [romfs/binwalk]=none     [romfs/binwalkv3]=diff
    [iso9660/unblob]=none   [iso9660/binwalk]=none   [iso9660/binwalkv3]=none
    [fat/unblob]=none       [fat/binwalk]=none       [fat/binwalkv3]=none
    [cpio/unblob]=diff      [cpio/binwalk]=ok        [cpio/binwalkv3]=diff
    [tar/unblob]=ok         [tar/binwalk]=ok         [tar/binwalkv3]=ok
    [zip/unblob]=diff       [zip/binwalk]=diff       [zip/binwalkv3]=diff
)

# Optional fixture subset (positional args). Empty => all fixtures.
WANT=("$@")
want_fixture() {
    [ ${#WANT[@]} -eq 0 ] && return 0
    for w in "${WANT[@]}"; do [ "$w" = "$1" ] && return 0; done
    return 1
}

declare -A type_for=( [rootfs.ext2]=ext2 [rootfs.ext3]=ext3 [rootfs.ext4]=ext4 \
    [rootfs.squashfs]=squashfs [rootfs.cramfs]=cramfs [rootfs.jffs2]=jffs2 \
    [rootfs.ubifs]=ubifs [rootfs.iso]=iso9660 [rootfs.fat]=fat \
    [rootfs.romfs]=romfs [rootfs.yaffs2]=yaffs \
    [rootfs.cpio]=cpio [rootfs.tar]=tar [rootfs.zip]=zip )

ROOTFS_SRC="$WORK/rootfs_src"
EXPECTED="$WORK/expected.json"
IMAGES="$WORK/images"
mkdir -p "$WORK"

echo "== building canonical rootfs and expectations =="
"$BEHAVIOR/build_rootfs.py" "$ROOTFS_SRC" "$EXPECTED" || exit 1

build_types=()
for f in "${FIXTURES[@]}"; do
    IFS='|' read -r name img <<<"$f"
    want_fixture "$name" || continue
    build_types+=("${type_for[$img]}")
done
echo "== building images: ${build_types[*]} =="
"$BEHAVIOR/make_images.sh" "$ROOTFS_SRC" "$IMAGES" "${build_types[@]}" || exit 1

# Run one (fixture image, extractor) cell. Echoes the outcome category and, for a
# 'diff', the mismatch count as "diff:N".
run_cell() {
    local img="$1" extractor="$2" name="$3"
    local outdir="$WORK/out_${name}_${extractor}"
    mkdir -p "$outdir"
    fakeroot_fw2tar "$IMAGES/$img" --output "$outdir/$name" --extractors "$extractor" \
        --timeout 120 --force >"$WORK/${name}_${extractor}.log" 2>&1
    local rootfs
    rootfs="$(find "$outdir" -name '*.rootfs.tar.gz' 2>/dev/null | head -1)"
    if [ -z "$rootfs" ] || [ ! -f "$rootfs" ]; then
        echo "none"; return
    fi
    local n
    n="$("$BEHAVIOR/check_behavior.py" --tar "$rootfs" --expected "$EXPECTED" \
          --name "$name" --report 2>"$WORK/${name}_${extractor}.diff")"
    case "$n" in
        0)   echo "ok" ;;
        ERR) echo "none" ;;
        *)   echo "diff:$n" ;;
    esac
}

declare -A CELL
for f in "${FIXTURES[@]}"; do
    IFS='|' read -r name img <<<"$f"
    want_fixture "$name" || continue
    if [ ! -f "$IMAGES/$img" ]; then
        for e in "${EXTRACTORS[@]}"; do CELL["$name/$e"]="skip"; done
        continue
    fi
    for e in "${EXTRACTORS[@]}"; do
        CELL["$name/$e"]="$(run_cell "$img" "$e" "$name")"
    done
done

# ---- render matrix ----
echo
printf '%-10s' "fixture"
for e in "${EXTRACTORS[@]}"; do printf ' %-12s' "$e"; done
printf '\n'
printf '%-10s' "----------"
for e in "${EXTRACTORS[@]}"; do printf ' %-12s' "------------"; done
printf '\n'
for f in "${FIXTURES[@]}"; do
    IFS='|' read -r name img <<<"$f"
    want_fixture "$name" || continue
    printf '%-10s' "$name"
    for e in "${EXTRACTORS[@]}"; do printf ' %-12s' "${CELL[$name/$e]}"; done
    printf '\n'
done
echo
echo "legend: ok=full fidelity  diff:N=rootfs with N metadata mismatches  none=no rootfs  skip=image not built"

# ---- gate against EXPECT (only fixtures that were run) ----
category() { case "$1" in diff:*) echo diff ;; *) echo "$1" ;; esac; }
failures=0
for key in "${!EXPECT[@]}"; do
    want_fixture "${key%%/*}" || continue
    want="${EXPECT[$key]}"
    got="$(category "${CELL[$key]:-MISSING}")"
    if [ "$got" != "$want" ]; then
        echo -e "${RED}REGRESSION $key: expected $want, got ${CELL[$key]:-MISSING}${END}"
        failures=$((failures+1))
    fi
done

echo
if [ "$failures" -eq 0 ]; then
    echo -e "${GREEN}behavior: matrix matches expectations${END}"
else
    echo -e "${RED}behavior: $failures cell(s) differ from expectations${END}"
fi
exit "$failures"
