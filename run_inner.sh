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

INFILE="$1"

if [ $# -lt 2 ]; then
    # If we  don't have 2 args, set OUTFILE to basename of INFILE + .tar.gz
    # Strip any suffix on INFILE before we add .tar.gz
    OUTFILE="${INFILE%.*}.tar.gz"
else
    OUTFILE="$2"
fi

# If we don't have 3 args, set SCRATCHDIR to /tmp
if [ $# -lt 3 ]; then
	SCRATCHDIR="/tmp/"
else
	SCRATCHDIR="$3"
fi

# Create our unblob + binwalk output file paths
OUTFILE_BASE="${OUTFILE%.tar.gz}"
UNBLOB_OUT="${OUTFILE_BASE}.unblob.tar.gz"
BINWALK_OUT="${OUTFILE_BASE}.binwalk.tar.gz"

echo "Running with INFILE=$INFILE OUTFILE=$OUTFILE SCRATCHDIR=$SCRATCHDIR"
echo "UNBLOB_OUT=$UNBLOB_OUT BINWALK_OUT=$BINWALK_OUT"

# TODO: we want to run these in parallel
# We'll do this by adding an & to the end of each command
# Then we'll wait for them to finish with wait.
# To get the PID of each command, we'll use $! after the command

fakeroot /extract/run_unblob.sh $INFILE $UNBLOB_OUT $SCRATCHDIR &
UNBLOB_PID=$!
fakeroot /extract/run_binwalk.sh $INFILE $BINWALK_OUT $SCRATCHDIR &
BINWALK_PID=$!

# Now we wait for both to finish
wait $UNBLOB_PID
wait $BINWALK_PID

# Now compare (TODO)
# We examine the two outputs and put the best one at $OUTFILE
