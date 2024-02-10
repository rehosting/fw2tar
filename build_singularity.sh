#!/bin/bash
set -eu

rm -f fw2tar*.sif || true

echo "1: Building docker container"
docker build -t fw2tar .

echo "2: Converting to singularity"
docker run -v /var/run/docker.sock:/var/run/docker.sock \
    -v $(pwd):/output \
    --privileged  -t \
    --rm quay.io/singularity/docker2singularity:v3.9.0 fw2tar

mv fw2tar*.sif fw2tar.sif
