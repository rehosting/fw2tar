#!/bin/bash

# Set up a temporary directory for our archive contents
WORKDIR=$(mktemp -d)
echo "Working directory: $WORKDIR"

# Create necessary directories and dummy file for symlink targets
mkdir -p "$WORKDIR/bin" "$WORKDIR/sbin" "$WORKDIR/usr/bin"
echo "This is BusyBox" > "$WORKDIR/bin/busybox"
echo "Dummy content" > "$WORKDIR/usr/bin/dummy"

# Descriptive names for symlink targets
# 1) Symlink in the same directory
ln -s busybox "$WORKDIR/bin/symlink_same_dir"

# 2) Symlink that points to a valid parent directory + file
ln -s ../bin/busybox "$WORKDIR/sbin/symlink_up_to_busybox"

# 3) Symlink with extra parent directories that would still be valid
ln -s ../../../bin/busybox "$WORKDIR/sbin/symlink_extra_up_to_busybox"

# 4) Absolute symlink to a file
ln -s /bin/busybox "$WORKDIR/bin/symlink_absolute"

# 5) Broken symlink (target does not exist)
ln -s non_existent_file "$WORKDIR/bin/symlink_broken"

# 6) Symlink pointing to another symlink (chained symlinks)
ln -s symlink_same_dir "$WORKDIR/bin/symlink_to_symlink"

# 7) Circular symlink (A -> B, B -> A)
ln -s symlink_circular_b "$WORKDIR/bin/symlink_circular_a"
ln -s symlink_circular_a "$WORKDIR/bin/symlink_circular_b"

# 8) Second symlink to busybox
ln -s busybox "$WORKDIR/bin/symlink_same_dir2"

# Navigate to the work directory
cd "$WORKDIR"

# Create the CPIO archive
find . | cpio -ov --format=newc > test_archive.cpio

# Move the archive to the current directory (assuming it's where the script is run)
mv test_archive.cpio "$OLDPWD"
cd "$OLDPWD"

# Clean up the working directory
rm -rf "$WORKDIR"
echo "Archive created: test_archive.cpio"

