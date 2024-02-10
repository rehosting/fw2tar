#!/bin/bash
set -eu
#docker build -t extract .

# Support any path
IN_PATH=$(readlink -f $1)
IN_DIR=$(dirname $IN_PATH)
IN_FILE=$(basename $IN_PATH)

docker run --rm -v ${IN_DIR}:/host -v $(pwd)/unblob:/unblob extract python3 /extract/extract.py /host/${IN_FILE}
