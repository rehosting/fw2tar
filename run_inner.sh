#!/bin/bash

set -e

unblob $@

# Newest file with a name that unblob could've made
#MOST_RECENT_FILE=$(find . -name "*_extract*" -type d -printf "%T@ %p\n" | sort -nr | awk '{print $2}' | head -n1)
set -o pipefail
MOST_RECENT_FILE="$(basename "${1}_extract")" || (echo "Failed to take basename of \"${1}\""; exit 1)
set +o pipefail

if [[ ! -d "$MOST_RECENT_FILE" ]]; then
  echo "Missing extraction ${1}_extract"
  exit 1
fi

# Search in there for the rootfs
POTENTIAL_DIRS=$(find $MOST_RECENT_FILE -type d \( -name "bin" -o -name "boot" -o -name "dev" -name "etc" -o -name "home" -o -name "lib" -o -name "media" -o -name "mnt" -o -name "opt" -o -name "proc" -o -name "root" -o -name "sbin" -o -name "sys" -o -name "tmp" -o -name "usr" -o -name "var" \) -exec dirname {} \; | sort | uniq -c |  awk '{ print length, $0 }' | sort -n -s | cut -d" " -f2- | sort -rg)

# If we found at least one, let's grab it
if [[ ! -z "${POTENTIAL_DIRS}" ]]; then
	# count dirname. Let's just grab the most likely
	FIRST_DIR=$(echo -e "$POTENTIAL_DIRS" | head -n1)
	FIRST_COUNT=$(echo "$FIRST_DIR" | awk '{print $1}')
	FIRST_ROOT="$(echo "$FIRST_DIR" | xargs echo -n | cut -d ' ' -f 2-)" # This is gross. Trim leading whitespace with xargs, then take everything after first space

	echo "Selecting $FIRST_ROOT as it matched $FIRST_COUNT critera"
	ROOT_DIR=$FIRST_ROOT

	tar cfz /data/output/${1}.tar.gz -C ${ROOT_DIR} .

else
	echo "FAILURE: no root directory found"
	exit 1
fi
