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

mkdir -p "${SCRATCHDIR}/binwalk_initial"
binwalk --run-as=root --preserve-symlinks -eM --log="${LOGBASE}.unblob.txt" -q "$INFILE" -C "${SCRATCHDIR}/binwalk_initial"

EXTRACT_DIR="${SCRATCHDIR}/binwalk_initial/"

# Find directories of interest, count occurrences, and prepare for sorting
POTENTIAL_DIRS=$(find "${EXTRACT_DIR}" -type d \( -name "bin" -o -name "boot" -o -name "dev" -o -name "etc" -o -name "home" -o -name "lib" -o -name "media" -o -name "mnt" -o -name "opt" -o -name "proc" -o -name "root" -o -name "sbin" -o -name "sys" -o -name "tmp" -o -name "usr" -o -name "var" \) \
| while read dirPath; do
    if [ "$(find "${dirPath}" -mindepth 1 -maxdepth 1 -type f | wc -l)" -gt 0 ]; then
        parentDir=$(dirname "${dirPath}")
        depth=$(echo "${parentDir}" | grep -o "/" | wc -l)
        size=$(du -s "${parentDir}" | cut -f1)
        echo "${size} ${depth} ${parentDir}"
    fi
done | sort -k1,1nr -k2,2n | head -n 1 | cut -d' ' -f3-)

# Check if we found at least one potential directory
if [[ -z "${POTENTIAL_DIRS}" ]]; then
    echo "FAILURE: no root directory found"
    exit 1
fi
# Extract the most likely root directory, now simply the first line of POTENTIAL_DIRS
FIRST_ROOT=$(echo -e "$POTENTIAL_DIRS" | head -n1)
echo "First root is $FIRST_ROOT"

# Now do 2nd pass to just get rootfs without extra extractions
REL_PATH=$(echo "$FIRST_ROOT" | sed "s|${SCRATCHDIR}/binwalk_initial||g")
DEPTH=$(echo "${REL_PATH}/" | grep -o "\.extracted/" | wc -l)

binwalk -d=$DEPTH --run-as=root --preserve-symlinks -eM -q "$INFILE" -C "${SCRATCHDIR}/binwalk_final"

# Now we want to tar up FIRST_DIR, but instead of being at /unblob_initial, it's at /final
FINAL_DIR=$(echo "$FIRST_ROOT" | sed "s|${SCRATCHDIR}/unblob_initial|${SCRATCHDIR}/binwalk_final|g")

# Warn on any _extract dirs. But our root dir is named _extract, so ignore that first one
find "${FINAL_DIR}/" -name "*_extract" -exec echo "WARNING: found _extract file in final dir: {}" \; | tail -n -1
tar czf "${OUTFILE}" --xattrs -C "${FINAL_DIR}" --exclude './dev' .
