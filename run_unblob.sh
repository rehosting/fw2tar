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

# Find directories of interest, count occurrences, and prepare for sorting
EXTRACT_DIR="${SCRATCHDIR}/unblob_initial/"

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
FIRST_ROOT=$(echo -e "$POTENTIAL_DIRS" | head -n1)
echo "First root is $FIRST_ROOT"

## Pull rootfs name out of debug log with extract command
ROOTFS_DIR=$(dirname "$FIRST_ROOT")
ROOTFS_NAME=$(basename "$FIRST_ROOT" | sed 's/_extract//g')
#EXTRACT_CMD=$(grep -o "Running extract command.*${ROOTFS_NAME}" "${LOGBASE}.unblob.txt" | head -n1)
## This line will contain command=... - grab that
#EXTRACT_CMD=$(echo "$EXTRACT_CMD" | sed 's/.*command=//g')
#echo "FINAL EXTRACT COMMAND: $EXTRACT_CMD"
#
## Now we'll change the extract command by swapping the output
## to a tempfile - we'll replace FIRST_ROOT with our own name
## and then run the command
#echo ""
#echo "Replace $ROOTFS_DIR/$ROOTFS_NAME with ${SCRATCHDIR}/unblob_final"
#echo ""
#
#EXTRACT_CMD=$(echo "$EXTRACT_CMD" | sed "s|${ROOTFS_DIR}/${ROOTFS_NAME}|${SCRATCHDIR}/unblob_final|g")
#echo "FINAL EXTRACT COMMAND: $EXTRACT_CMD"
#eval $EXTRACT_CMD
# There should be a single directory in scratchdir / unblob_final - that's our final dir
#FINAL_DIR="${SCRATCHDIR}/unblob_final/$(ls "${SCRATCHDIR}/unblob_final")"

# Double extract is too complicated. Just delete the _extract dirs and compress that
mkdir -p "${SCRATCHDIR}/unblob_final"
mv "${FIRST_ROOT}" "${SCRATCHDIR}/unblob_final"

# We have a single directory in unblob_final - that's our final dir
TARGET=$(ls "${SCRATCHDIR}/unblob_final")

# Tar, but exclude anything with _extract in the name
tar czf "${OUTFILE}" --xattrs -C "${SCRATCHDIR}/unblob_final/${TARGET}"  --exclude "*_extract" --exclude "./dev" .
