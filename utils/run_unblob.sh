#!/bin/bash
set -eu

if [ ! -f "$1" ]; then
  echo "Error: Input file not found"
  exit 1
fi

# Resolve argument path and map into container appropriately
IN_PATH=$(readlink -f "$1")
IN_DIR=$(dirname "$IN_PATH")
IN_FILE=$(basename "$IN_PATH")

if [ -d unblob ]; then
  echo "Using local unblob"
  docker run --rm -v ${IN_DIR}:/host -v $(pwd)/unblob:/unblob \
  fw2tar fakeroot unblob -v "/host/${IN_FILE}"
else
  docker run --rm -v ${IN_DIR}:/host fw2tar \
  fakeroot unblob -v "/host/${IN_FILE}"
fi