#!/bin/bash

set -eu

# USAGE ./run_inner.sh /INPUT/FW /SCRATCH/DIR /OUT/BASENAME
INFILE=$1     # /firmware/dir/vendor/blob.bin
SCRATCHDIR=$2 # /tmp
OUTBASE=$3    # /vendor/blob.bin (e.g., outputs written into /data/output/vendor/blob.bin.tar.gz)
shift 3

# Newest file with a name that unblob could've made
#MOST_RECENT_FILE=$(find . -name "*_extract*" -type d -printf "%T@ %p\n" | sort -nr | awk '{print $2}' | head -n1)
set -o pipefail
EXTRACTNAME="$(basename "${INFILE}_extract")" || (echo "Failed to take basename of \"${1}\""; exit 1)
set +o pipefail

unblob --log="/data/output/${OUTBASE}_log.txt" --extract-dir="${SCRATCHDIR}" "$INFILE"

# Search in there for the rootfs
POTENTIAL_DIRS=$(find "${SCRATCHDIR}/"*_extract -type d \( -name "bin" -o -name "boot" -o -name "dev" -name "etc" -o -name "home" -o -name "lib" -o -name "media" -o -name "mnt" -o -name "opt" -o -name "proc" -o -name "root" -o -name "sbin" -o -name "sys" -o -name "tmp" -o -name "usr" -o -name "var" \) -exec dirname {} \; | sort | uniq -c |  awk '{ print length, $0 }' | sort -n -s | cut -d" " -f2- | sort -rg)

# If we found at least one, let's grab it
if [[ ! -z "${POTENTIAL_DIRS}" ]]; then
	# count dirname. Let's just grab the most likely
	FIRST_DIR=$(echo -e "$POTENTIAL_DIRS" | head -n1)
	FIRST_COUNT=$(echo "$FIRST_DIR" | awk '{print $1}')
	FIRST_ROOT="$(echo "$FIRST_DIR" | xargs echo -n | cut -d ' ' -f 2-)" # This is gross. Trim leading whitespace with xargs, then take everything after first space

	#echo "Selecting $FIRST_ROOT as it matched $FIRST_COUNT critera. Writing out to /data/output/${OUTBASE}.tar.gz"
	tar czf "/data/output/${OUTBASE}.tar.gz" --xattrs -C "${FIRST_ROOT}" .

else
	echo "FAILURE: no root directory found"
	exit 1
fi
