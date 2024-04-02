#!/bin/bash
set -eu

# Function to display usage
usage() {
    echo "$0 is a simple shell wrapper around a dockerized fw2tar.py with the following usage:"
    docker run --rm rehosting/fw2tar:latest python3 /fw2tar.py --help
    exit 1
}

# Check for minimum number of arguments
if [ $# -lt 1 ]; then
    usage
fi

# Initialize variables
OTHER_ARGS=()

# The first argument is always the input file
INFILE="$1"
shift

# If the next argument exists and doesn't start with a dash, it's the output file
if [ $# -gt 0 ] && [[ "$1" != -* ]]; then
    OUTFILE="$1"
    shift
else
    OUTFILE="" # No output file specified
fi

# The rest of the arguments are collected into OTHER_ARGS
OTHER_ARGS=("$@")

# Validate input file exists
if [ ! -f "$INFILE" ]; then
    echo "Error: Input file not found. Did you provide the input file as the FIRST argument?"
    exit 1
fi

# Resolve input file path to ensure correct Docker volume mapping
IN_PATH=$(readlink -f "$INFILE")
IN_DIR=$(dirname "$IN_PATH")
IN_FILE_BASENAME=$(basename "$IN_PATH")

# Prepare Docker command
DOCKER_CMD="docker run --rm -v ${IN_DIR}:/hostin"

# Setup output file directory mapping if specified
if [ ! -z "$OUTFILE" ]; then
    OUT_PATH=$(readlink -f "$OUTFILE")
    OUT_DIR=$(dirname "$OUT_PATH")
    OUT_FILE_BASENAME=$(basename "$OUT_PATH")
    DOCKER_CMD+=" -v ${OUT_DIR}:/hostout"
    # Pass outfile as an optional positional argument to the Python script
    OTHER_ARGS=("/hostout/${OUT_FILE_BASENAME}" "${OTHER_ARGS[@]}")
fi

# Finalize Docker command with input file and other arguments
DOCKER_CMD+=" rehosting/fw2tar:latest fakeroot python3 /fw2tar.py \"/hostin/${IN_FILE_BASENAME}\" ${OTHER_ARGS[*]}"

# Execute the Docker command
eval $DOCKER_CMD
