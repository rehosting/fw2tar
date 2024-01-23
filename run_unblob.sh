#!/bin/bash
set -eu

# Function to display usage message
usage() {
    echo "Usage: $0 [INFILE] [OUTFILE] [SCRATCHDIR]"
    exit 1
}

# Check if at least one argument is provided
if [ $# -lt 1 ]; then
    usage
fi

# Assigning first argument to INFILE
INFILE=$1
OUTFILE="$2"
SCRATCHDIR="$3"
LOGBASE="${INFILE%.*}"

mkdir -p "${SCRATCHDIR}/unblob_initial"
unblob --log "${LOGBASE}.unblob.txt" --extract-dir="${SCRATCHDIR}/unblob_initial" "$INFILE"

# Search in there for the rootfs
POTENTIAL_DIRS=$(find "${SCRATCHDIR}/unblob_initial/"*_extract -type d \( -name "bin" -o -name "boot" -o -name "dev" -name "etc" -o -name "home" -o -name "lib" -o -name "media" -o -name "mnt" -o -name "opt" -o -name "proc" -o -name "root" -o -name "sbin" -o -name "sys" -o -name "tmp" -o -name "usr" -o -name "var" \) -exec dirname {} \; | sort | uniq -c |  awk '{ print length, $0 }' | sort -n -s | cut -d" " -f2- | sort -rg)

# If we found at least one, let's grab it
if [[ -z "${POTENTIAL_DIRS}" ]]; then
	echo "FAILURE: no root directory found"
	exit 1
fi

# count dirname. Let's just grab the most likely
FIRST_DIR=$(echo -e "$POTENTIAL_DIRS" | head -n1)
FIRST_COUNT=$(echo "$FIRST_DIR" | awk '{print $1}')
FIRST_ROOT="$(echo "$FIRST_DIR" | xargs echo -n | cut -d ' ' -f 2-)" # This is gross. Trim leading whitespace with xargs, then take everything after first space

# Let's identify root fileystem, it will be in dirname FIRST_ROOT and named FIRST_ROOT with the trailing _extract removed
ROOTFS_DIR=$(dirname "$FIRST_ROOT")
ROOTFS_NAME=$(basename "$FIRST_ROOT" | sed 's/_extract//g')

# If you're wondering how many extra files are in the extracted rootfs at this point, uncomment this:
#find "${FIRST_ROOT}" -name "*_extract"

# We don't like these files since they wouldn't be in a real rootfs so we'll do a clean extraction
# configured to avoid recursing within the rootfs

# New approach: In the parent of the rootfs we expect to see an unextracted file of some type.
# Let's binwalk that directly with a depth of 1.

# We expect to have a file named ROOTFS_NAME in ROOTFS_DIR
unblob --extract-dir="${SCRATCHDIR}/unblob_final" -d 1 "$ROOTFS_DIR/$ROOTFS_NAME"
# There should be a single directory in scratchdir / unblob_final - that's our final dir
FINAL_DIR="${SCRATCHDIR}/unblob_final/$(ls "${SCRATCHDIR}/unblob_final")"

# OLD APPROACH: do a 2nd extraction with a depth limited based on the number of '_extract' strings in the path to the rootfs
# This ran into issues where the depth argument didn't cleanly map onto the number of _extract directories. Not sure why
# Also it was probably slower.
#
## Second pass: re-extract with a depth limit to avoid extracting within our target rootfs
## We could find and rm -rf anything named _extracted in our root. But what if an original file had that name?
## Instead we'll just re-extract with a depth limit that's set to the depth of our target rootfs
## Note depth refers to the number of extractions, not the depth of the filesystem.
#
## Let's find the number of extractions we need to do
## Now we want to drop the prefix of "$SCRATCHDIR/unblob_initial"
## and find the number of _extract strings in the path.
## These paths are generated based on user's path and filesystem
## types so it's unlikely they'll have an extra _extract.
#REL_PATH=$(echo "$FIRST_ROOT" | sed "s|${SCRATCHDIR}/unblob_initial||g")
#DEPTH=$(echo "${REL_PATH}/" | grep -o "_extract/" | wc -l)
#
#unblob --extract-dir="${SCRATCHDIR}/unblob_final" -d $DEPTH "$INFILE"

# Now we want to tar up FIRST_DIR, but instead of being at /unblob_initial, it's at /final
#FINAL_DIR=$(echo "$FIRST_ROOT" | sed "s|${SCRATCHDIR}/unblob_initial|${SCRATCHDIR}/unblob_final|g")

# Warn on any _extract dirs. But our root dir is named _extract, so ignore that first one
find "${FINAL_DIR}/" -name "*_extract" -exec echo "WARNING: found _extract file in final dir: {}" \; | tail -n -1
tar czf "${OUTFILE}" --xattrs -C "${FINAL_DIR}" .