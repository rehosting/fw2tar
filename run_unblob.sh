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

echo "First root is $FIRST_ROOT"
#
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
tar czf "${OUTFILE}" --xattrs -C "${SCRATCHDIR}/unblob_final/${TARGET}"  --exclude "*_extract" .