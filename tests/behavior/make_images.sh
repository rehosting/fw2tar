#!/bin/bash
# Build synthetic filesystem images from the canonical rootfs, unprivileged.
#
# Usage: make_images.sh <rootfs_dir> <out_dir> [fstype ...]
#   fstype defaults to all supported types.
#
# Each tool here populates an image directly from a directory tree without
# mounting or root, preserving the source mode bits where the filesystem can.
# Types map 1:1 onto an unblob filesystem handler (see tests/BEHAVIOR.md).
set -eu

ROOT="${1:?rootfs dir required}"
OUT="${2:?output dir required}"
shift 2
TYPES=("$@")
if [ ${#TYPES[@]} -eq 0 ]; then
    TYPES=(ext2 ext3 ext4 squashfs cramfs jffs2 ubifs iso9660 fat romfs yaffs cpio tar zip)
fi

mkdir -p "$OUT"
TMP="$OUT/.tmp"; mkdir -p "$TMP"

build_extfs() {  # $1 = ext2|ext3|ext4
    local t="$1"
    rm -f "$OUT/rootfs.$t"
    mke2fs -q -F -t "$t" -b 1024 -d "$ROOT" "$OUT/rootfs.$t" 16384
    echo "built $OUT/rootfs.$t"
}

build_squashfs() {
    rm -f "$OUT/rootfs.squashfs"
    # keep source perms/owners (do NOT use -all-root)
    mksquashfs "$ROOT" "$OUT/rootfs.squashfs" -noappend -no-progress >/dev/null
    echo "built $OUT/rootfs.squashfs"
}

build_cramfs() {
    rm -f "$OUT/rootfs.cramfs"
    # Host-endian (little-endian) only. Big-endian (issue #5) needs a cross-endian
    # builder and is tracked as a known gap.
    mkfs.cramfs "$ROOT" "$OUT/rootfs.cramfs" 2>/dev/null
    echo "built $OUT/rootfs.cramfs"
}

build_jffs2() {
    rm -f "$OUT/rootfs.jffs2"
    # little-endian, 128KiB eraseblock, padded so the carver sees a full image.
    mkfs.jffs2 -r "$ROOT" -o "$OUT/rootfs.jffs2" -l -e 0x20000 -p >/dev/null 2>&1
    echo "built $OUT/rootfs.jffs2"
}

build_ubifs() {
    rm -f "$OUT/rootfs.ubifs"
    # min I/O 2048, PEB 128KiB => LEB 129024, then wrap in a UBI image via ubinize.
    mkfs.ubifs -r "$ROOT" -m 2048 -e 0x1f800 -c 2047 -o "$TMP/ubifs.img" >/dev/null 2>&1
    cat >"$TMP/ubinize.cfg" <<EOF
[rootfs]
mode=ubi
image=$TMP/ubifs.img
vol_id=0
vol_type=dynamic
vol_name=rootfs
vol_flags=autoresize
EOF
    ubinize -o "$OUT/rootfs.ubifs" -m 2048 -p 0x20000 "$TMP/ubinize.cfg" >/dev/null 2>&1
    echo "built $OUT/rootfs.ubifs"
}

build_iso9660() {
    rm -f "$OUT/rootfs.iso"
    # -R = Rock Ridge: carries unix modes, ownership and symlinks on iso9660.
    genisoimage -R -quiet -o "$OUT/rootfs.iso" "$ROOT"
    echo "built $OUT/rootfs.iso"
}

build_romfs() {
    rm -f "$OUT/rootfs.romfs"
    # romfs is read-only and stores only file type + the executable bit, not full
    # rwx modes — this characterizes that limitation.
    genromfs -d "$ROOT" -f "$OUT/rootfs.romfs"
    echo "built $OUT/rootfs.romfs"
}

build_yaffs() {
    if ! command -v mkyaffs2image >/dev/null 2>&1; then
        echo "skip yaffs: mkyaffs2image not available in this image"
        return 0
    fi
    rm -f "$OUT/rootfs.yaffs2"
    mkyaffs2image "$ROOT" "$OUT/rootfs.yaffs2" >/dev/null 2>&1
    echo "built $OUT/rootfs.yaffs2"
}

build_cpio() {
    rm -f "$OUT/rootfs.cpio"
    # newc format carries unix modes, special bits and symlinks.
    ( cd "$ROOT" && find . -mindepth 1 -print0 | LC_ALL=C sort -z \
        | cpio --null -o -H newc --quiet ) >"$OUT/rootfs.cpio"
    echo "built $OUT/rootfs.cpio"
}

build_tar() {
    rm -f "$OUT/rootfs.tar"
    # tar preserves modes, special bits and symlinks.
    tar --numeric-owner -C "$ROOT" -cf "$OUT/rootfs.tar" .
    echo "built $OUT/rootfs.tar"
}

build_zip() {
    rm -f "$OUT/rootfs.zip"
    # zip stores unix modes (external attrs) and symlinks (-y); whether they
    # survive depends on the extractor (unblob uses 7z for zip).
    ( cd "$ROOT" && zip -qry -X "$OUT/rootfs.zip" . )
    echo "built $OUT/rootfs.zip"
}

build_fat() {
    rm -f "$OUT/rootfs.fat"
    # FAT has no unix permissions or symlinks; this characterizes the *limitation*.
    # mcopy can't follow symlinks, so copy only the real files/dirs.
    truncate -s 16M "$OUT/rootfs.fat"
    mkfs.vfat -F 16 "$OUT/rootfs.fat" >/dev/null
    ( cd "$ROOT" && mcopy -s -i "$OUT/rootfs.fat" -- * :: ) >/dev/null 2>&1 || true
    echo "built $OUT/rootfs.fat"
}

# --- nested fixtures: a real filesystem carried INSIDE another container ---
# These mirror how firmware actually ships (a rootfs image embedded in a boot
# filesystem / disk image) and exercise fw2tar's job of recursing through the
# outer layer, selecting the inner Linux rootfs, and emitting it cleanly without
# the extractor's nesting scaffolding.

build_squashfs_in_ext4() {  # inner squashfs, wrapped in an outer ext4
    rm -f "$OUT/rootfs.sqfs_in_ext4"
    local stage="$TMP/sqfs_in_ext4"
    rm -rf "$stage"; mkdir -p "$stage"
    mksquashfs "$ROOT" "$stage/rootfs.squashfs" -noappend -no-progress >/dev/null
    mke2fs -q -F -t ext4 -b 1024 -d "$stage" "$OUT/rootfs.sqfs_in_ext4" 16384
    echo "built $OUT/rootfs.sqfs_in_ext4"
}

build_two_squashfs_in_tar() {  # TWO inner squashfs rootfs, wrapped in an outer tar
    # Exercises --primary-limit > 1: firmware that splits its rootfs across
    # several filesystem images (e.g. a main image plus an /opt image). The
    # second tree drops a few executables so the candidate ranking (file node
    # count) is deterministic: rootfs_a wins as primary, rootfs_b is the
    # secondary. Used by tests/contract/run.sh, not the behavior matrix.
    rm -f "$OUT/rootfs.two_sqfs_in_tar"
    local stage="$TMP/two_sqfs_in_tar"
    rm -rf "$stage"; mkdir -p "$stage"
    mksquashfs "$ROOT" "$stage/rootfs_a.squashfs" -noappend -no-progress >/dev/null
    local root_b="$TMP/two_sqfs_root_b"
    rm -rf "$root_b"
    cp -a "$ROOT" "$root_b"
    rm -f "$root_b"/usr/bin/prog9 "$root_b"/usr/bin/prog10 "$root_b"/usr/bin/prog11
    mksquashfs "$root_b" "$stage/rootfs_b.squashfs" -noappend -no-progress >/dev/null
    tar --numeric-owner -C "$stage" -cf "$OUT/rootfs.two_sqfs_in_tar" .
    echo "built $OUT/rootfs.two_sqfs_in_tar"
}

build_ext4_in_tar() {  # inner ext4, wrapped in an outer tar
    rm -f "$OUT/rootfs.ext4_in_tar"
    local stage="$TMP/ext4_in_tar"
    rm -rf "$stage"; mkdir -p "$stage"
    mke2fs -q -F -t ext4 -b 1024 -d "$ROOT" "$stage/rootfs.ext4" 16384
    tar --numeric-owner -C "$stage" -cf "$OUT/rootfs.ext4_in_tar" .
    echo "built $OUT/rootfs.ext4_in_tar"
}

for t in "${TYPES[@]}"; do
    case "$t" in
        ext2|ext3|ext4) build_extfs "$t" ;;
        squashfs_in_ext4) build_squashfs_in_ext4 ;;
        two_squashfs_in_tar) build_two_squashfs_in_tar ;;
        ext4_in_tar)      build_ext4_in_tar ;;
        squashfs) build_squashfs ;;
        cramfs)   build_cramfs ;;
        jffs2)    build_jffs2 ;;
        ubifs)    build_ubifs ;;
        iso9660)  build_iso9660 ;;
        fat)      build_fat ;;
        romfs)    build_romfs ;;
        yaffs)    build_yaffs ;;
        cpio)     build_cpio ;;
        tar)      build_tar ;;
        zip)      build_zip ;;
        *) echo "unknown fstype: $t" >&2; exit 2 ;;
    esac
done

rm -rf "$TMP"
