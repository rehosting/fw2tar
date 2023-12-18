#!/bin/bash
set -eu

# Function to display usage message
usage() {
    echo "Usage: $0 INFILE [OUTFILE] [SCRATCHDIR]"
    echo "  INFILE: Path to input file, e.g., /firmware/dir/vendor/blob.bin"
    echo "  OUTFILE: Path for output file. Defaults to basename of INFILE + .tar.gz"
    echo "           .tar.gz suffix will be added if not provided"
    echo "           A file with the .log suffix will be created with the same name"
    echo "  SCRATCHDIR: Scratch directory to use. Defaults to /tmp/"
    exit 1
}

# Check if at least one argument is provided
if [ $# -lt 1 ]; then
    usage
fi

# Assigning first argument to INFILE
INFILE=$1

# If we have a second argument ...
if [ $# -gt 1 ]; then
	OUTFILE="$2"

	# Create log file name by replacing .tar.gz with .log
	LOGFILE="${OUTFILE%.*}.log"

	# If it doesn't end with .tar.gz, add it
	if [[ ! "$OUTFILE" =~ \.tar\.gz$ ]]; then
		OUTFILE="${OUTFILE}.tar.gz"
	fi

else
 	# No argument, rewrite INFILE to have .tar.gz suffix
	OUTFILE="${INFILE%.*}.tar.gz"
	LOGFILE="${INFILE%.*}.log"
fi

# If outfile is infile, error
if [ "$INFILE" = "$OUTFILE" ]; then
	echo "ERROR: INFILE and OUTFILE are the same"
	usage
fi

if [ $# -gt 2 ]; then
	SCRATCHDIR="$3"
else
	# No argument, use /tmp
	SCRATCHDIR=/tmp/
fi


# Newest file with a name that unblob could've made
#MOST_RECENT_FILE=$(find . -name "*_extract*" -type d -printf "%T@ %p\n" | sort -nr | awk '{print $2}' | head -n1)
set -o pipefail
EXTRACTNAME="$(basename "${INFILE}_extract")" || (echo "Failed to take basename of \"${1}\""; exit 1)
set +o pipefail

mkdir -p "${SCRATCHDIR}/initial"

unblob --log "${LOGFILE}.txt" --extract-dir="${SCRATCHDIR}/initial" "$INFILE"
#unblob --extract-dir="${SCRATCHDIR}/initial" "$INFILE"

# Search in there for the rootfs
POTENTIAL_DIRS=$(find "${SCRATCHDIR}/initial/"*_extract -type d \( -name "bin" -o -name "boot" -o -name "dev" -name "etc" -o -name "home" -o -name "lib" -o -name "media" -o -name "mnt" -o -name "opt" -o -name "proc" -o -name "root" -o -name "sbin" -o -name "sys" -o -name "tmp" -o -name "usr" -o -name "var" \) -exec dirname {} \; | sort | uniq -c |  awk '{ print length, $0 }' | sort -n -s | cut -d" " -f2- | sort -rg)

# If we found at least one, let's grab it
if [[ -z "${POTENTIAL_DIRS}" ]]; then
	echo "FAILURE: no root directory found"
	exit 1
fi

# count dirname. Let's just grab the most likely
FIRST_DIR=$(echo -e "$POTENTIAL_DIRS" | head -n1)
FIRST_COUNT=$(echo "$FIRST_DIR" | awk '{print $1}')
FIRST_ROOT="$(echo "$FIRST_DIR" | xargs echo -n | cut -d ' ' -f 2-)" # This is gross. Trim leading whitespace with xargs, then take everything after first space

# If you're wondering why we do 2 extractions, uncomment this and look at the extra files we're keeping out of our final tarball
#echo "Extra extractions: INITIAL_DIR=${FIRST_ROOT}"
#find "${FIRST_ROOT}" -name "*_extract"
#echo "Selecting $FIRST_ROOT as it matched $FIRST_COUNT critera. Writing out to /data/output/${OUTFILE}.tar.gz"
#tar czf "/data/output/${OUTFILE}.tar.gz" --xattrs -C "${FIRST_ROOT}" .

# Second pass: re-extract with a depth limit to avoid extracting within our target rootfs
# We could find and rm -rf anything named _extracted in our root. But what if an original file had that name?
# Instead we'll just re-extract with a depth limit that's set to the depth of our target rootfs
DEPTH=$(echo "$FIRST_ROOT" | tr -cd '/' | wc -c)
SCRATCHDEPTH=$(echo "$SCRATCHDIR/initial" | tr -cd '/' | wc -c)
DEPTH=$((DEPTH - SCRATCHDEPTH - 1))

unblob --extract-dir="${SCRATCHDIR}/final" -d $DEPTH "$INFILE"

# Now we want to tar up FIRST_DIR, but instead of being at /initial, it's at /final
FINAL_DIR=$(echo "$FIRST_ROOT" | sed "s|${SCRATCHDIR}/initial|${SCRATCHDIR}/final|g")

# Warn on any _extract dirs. But our root dir is named _extract, so ignore that first one
find "${FINAL_DIR}/" -name "*_extract" -exec echo "WARNING: found _extract file in final dir: {}" \; | tail -n -1
tar czf "${OUTFILE}" --xattrs -C "${FINAL_DIR}" .