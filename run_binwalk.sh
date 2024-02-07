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

# Search in there for the rootfs
POTENTIAL_DIRS=$(find "${SCRATCHDIR}/binwalk_initial/" -type d \( -name "bin" -o -name "boot" -o -name "dev" -name "etc" -o -name "home" -o -name "lib" -o -name "media" -o -name "mnt" -o -name "opt" -o -name "proc" -o -name "root" -o -name "sbin" -o -name "sys" -o -name "tmp" -o -name "usr" -o -name "var" \) -exec dirname {} \; | sort | uniq -c |  awk '{ print length, $0 }' | sort -n -s | cut -d" " -f2- | sort -rg)

# If we found at least one, let's grab it
if [[ -z "${POTENTIAL_DIRS}" ]]; then
	echo "FAILURE: no root directory found"
	exit 1
fi

# count dirname. Let's just grab the most likely
FIRST_DIR=$(echo -e "$POTENTIAL_DIRS" | head -n1)
FIRST_COUNT=$(echo "$FIRST_DIR" | awk '{print $1}')
FIRST_ROOT="$(echo "$FIRST_DIR" | xargs echo -n | cut -d ' ' -f 2-)" # This is gross. Trim leading whitespace with xargs, then take everything after first space

# Now do 2nd pass to just get rootfs without extra extractions
REL_PATH=$(echo "$FIRST_ROOT" | sed "s|${SCRATCHDIR}/binwalk_initial||g")
DEPTH=$(echo "${REL_PATH}/" | grep -o "\.extracted/" | wc -l)

binwalk -d=$DEPTH --run-as=root --preserve-symlinks -eM -q "$INFILE" -C "${SCRATCHDIR}/binwalk_final"

# Now we want to tar up FIRST_DIR, but instead of being at /unblob_initial, it's at /final
FINAL_DIR=$(echo "$FIRST_ROOT" | sed "s|${SCRATCHDIR}/unblob_initial|${SCRATCHDIR}/binwalk_final|g")

# Warn on any _extract dirs. But our root dir is named _extract, so ignore that first one
find "${FINAL_DIR}/" -name "*_extract" -exec echo "WARNING: found _extract file in final dir: {}" \; | tail -n -1
tar czf "${OUTFILE}" --xattrs -C "${FINAL_DIR}" --exclude './dev' .
