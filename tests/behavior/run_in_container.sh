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
    # nested: a real filesystem carried inside another container (firmware shape).
    # Exercises recursion + inner-rootfs selection; gated for cruft below.
    "squashfs_in_ext4|rootfs.sqfs_in_ext4"
    "ext4_in_tar|rootfs.ext4_in_tar"
)

# Expected outcome per "<fixture>/<extractor>" cell:
#   ok      rootfs produced, every entry matches expectations (full fidelity)
#   diff:N  rootfs produced but N metadata properties lost (a known bug); the
#           count is PINNED — both worsening (N grows) and partially fixing it
#           (N shrinks) trip the gate, so a real fork fix forces a re-baseline
#           or a promotion to `ok`
#   none    no rootfs produced (extractor can't / metadata lost so it isn't detected)
#   skip    fixture image could not be built (e.g. yaffs without mkyaffs2image)
# Cells with no entry are reported but NOT gated (yaffs is left ungated because its
# image only builds when mkyaffs2image is present). Captured 2026-06-22 against the
# Docker image; re-baselined for the Nix image (binwalk v3.1.0 from nixpkgs now
# extracts cramfs/cpio where the older build produced no rootfs). 2026-06-23:
# unblob/binwalk now extract an iso9660 rootfs (Rock Ridge drops suid/sgid/sticky
# + some symlinks); pinned exact mismatch counts for every known-bug cell.
declare -A EXPECT=(
    [squashfs/unblob]=ok    [squashfs/binwalk]=ok    [squashfs/binwalkv3]=ok
    [cramfs/unblob]=ok      [cramfs/binwalk]=ok      [cramfs/binwalkv3]=diff:6
    [ubifs/unblob]=ok       [ubifs/binwalk]=ok       [ubifs/binwalkv3]=none
    [jffs2/unblob]=ok       [jffs2/binwalk]=diff:3   [jffs2/binwalkv3]=diff:3
    [ext2/unblob]=ok        [ext2/binwalk]=none      [ext2/binwalkv3]=none
    [ext3/unblob]=ok        [ext3/binwalk]=none      [ext3/binwalkv3]=none
    [ext4/unblob]=ok        [ext4/binwalk]=none      [ext4/binwalkv3]=none
    [romfs/unblob]=none     [romfs/binwalk]=none     [romfs/binwalkv3]=diff:23
    [iso9660/unblob]=diff:6 [iso9660/binwalk]=diff:6 [iso9660/binwalkv3]=none
    [fat/unblob]=none       [fat/binwalk]=none       [fat/binwalkv3]=none
    [cpio/unblob]=ok        [cpio/binwalk]=ok        [cpio/binwalkv3]=diff:6
    [tar/unblob]=ok         [tar/binwalk]=ok         [tar/binwalkv3]=ok
    [zip/unblob]=diff:6     [zip/binwalk]=diff:4     [zip/binwalkv3]=diff:6
    # nested fixtures (baselined 2026-06-28): the inner rootfs must be selected
    # through the outer container with full fidelity for unblob.
    [squashfs_in_ext4/unblob]=ok  [squashfs_in_ext4/binwalk]=none [squashfs_in_ext4/binwalkv3]=ok
    [ext4_in_tar/unblob]=ok       [ext4_in_tar/binwalk]=none      [ext4_in_tar/binwalkv3]=none
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
    [rootfs.cpio]=cpio [rootfs.tar]=tar [rootfs.zip]=zip \
    [rootfs.sqfs_in_ext4]=squashfs_in_ext4 [rootfs.ext4_in_tar]=ext4_in_tar )

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
# Cells expected as a known bug pin the EXACT mismatch count (diff:N): both
# worsening a bug (diff:3 -> diff:5) and partially fixing one (diff:3 -> diff:1)
# trip the gate, forcing a re-baseline or a fixture promotion to golden. Cells
# expected ok/none/skip are matched by category (the count is irrelevant there).
category() { case "$1" in diff:*) echo diff ;; *) echo "$1" ;; esac; }
failures=0
for key in "${!EXPECT[@]}"; do
    want_fixture "${key%%/*}" || continue
    want="${EXPECT[$key]}"
    raw="${CELL[$key]:-MISSING}"
    case "$want" in
        diff:*) got="$raw" ;;                  # exact mismatch count required
        *)      got="$(category "$raw")" ;;    # ok / none / skip: category only
    esac
    if [ "$got" != "$want" ]; then
        echo -e "${RED}REGRESSION $key: expected $want, got ${CELL[$key]:-MISSING}${END}"
        failures=$((failures+1))
    fi
done

# ---- no-cruft gate ----
# A correct fw2tar output is ONLY the real rootfs: no extractor scaffolding
# (unblob's `*.extracted` wrappers, `<offset>-<offset>` chunk dirs, leftover
# container images from a nested extraction, etc.). For cells that produce a
# full-fidelity rootfs, additionally assert there are zero unexpected entries
# (--strict-extras; lost+found is allowed). Nested fixtures are the important
# case — that is where the scaffolding would otherwise leak in.
NOCRUFT_CELLS=(squashfs/unblob cpio/unblob squashfs_in_ext4/unblob ext4_in_tar/unblob)
echo
echo "== no-cruft gate =="
for cell in "${NOCRUFT_CELLS[@]}"; do
    name="${cell%%/*}"; extractor="${cell##*/}"
    want_fixture "$name" || continue
    rootfs="$(find "$WORK/out_${name}_${extractor}" -name '*.rootfs.tar.gz' 2>/dev/null | head -1)"
    if [ -z "$rootfs" ] || [ ! -f "$rootfs" ]; then
        echo -e "${RED}NO-CRUFT $cell: no rootfs produced${END}"
        failures=$((failures+1)); continue
    fi
    n="$("$BEHAVIOR/check_behavior.py" --tar "$rootfs" --expected "$EXPECTED" \
          --name "$name" --strict-extras --report 2>"$WORK/${name}_${extractor}.cruft")"
    if [ "$n" = "0" ]; then
        echo -e "${GREEN}no-cruft $cell: clean${END}"
    else
        echo -e "${RED}NO-CRUFT $cell: $n unexpected/mismatched entr(y/ies)${END}"
        sed 's/^/    /' "$WORK/${name}_${extractor}.cruft" >&2 2>/dev/null || true
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
