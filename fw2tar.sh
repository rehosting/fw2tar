#!/bin/bash
set -eu

if [ $# -lt 1 ]; then
  echo "Usage: $0 [--extractors=binwalk,unblob] <path to firmware file>"
  exit 1
fi

# If we have --extractors flag
EXTRACTORS="binwalk,unblob"
if [ $# -eq 2 ]; then
  if [[ $1 == --extractors=* ]]; then
    EXTRACTORS=$(echo $1 | cut -d= -f2)
    shift
  fi
fi

if [ ! -f "$1" ]; then
  echo "Error: Input file not found"
  exit 1
fi

# Resolve argument path and map into container appropriately
IN_PATH=$(readlink -f "$1")
IN_DIR=$(dirname "$IN_PATH")
IN_FILE=$(basename "$IN_PATH")

docker run --rm -v ${IN_DIR}:/host -v $(pwd)/unblob:/unblob fw2tar fakeroot python3 /fw2tar.py --extractors=${EXTRACTORS} "/host/${IN_FILE}"
