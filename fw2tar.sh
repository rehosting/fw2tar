#!/bin/bash
set -eu

if [ $# -ne 1 ]; then
  echo "Usage: $0 <path to firmware file>"
  exit 1
fi

# Resolve argument path and map into container appropriately
IN_PATH=$(readlink -f $1)
IN_DIR=$(dirname $IN_PATH)
IN_FILE=$(basename $IN_PATH)

docker run --rm -v ${IN_DIR}:/host -v $(pwd)/unblob:/unblob extract /host/${IN_FILE}
