#!/bin/bash
set -eu

if [ $# -lt 1 ]; then
  exit 1
fi

if [ ! -f "$1" ]; then
  echo "Error: Input file not found"
  exit 1
fi

# Resolve argument path and map into container appropriately
IN_PATH=$(readlink -f "$1")
IN_DIR=$(dirname "$IN_PATH")
IN_FILE=$(basename "$IN_PATH")

# Need no entrypoint on dockerfile
docker run --rm -v ${IN_DIR}:/host fw2tar fakeroot binwalk -0 root -1 --preserve-symlinks -eM /host/${IN_FILE} -C /host/${IN_FILE}_extract.binwalk