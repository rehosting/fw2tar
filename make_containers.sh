#!/bin/bash

rm -f extract*.sif

echo "1: Building docker container"
docker build -t extract .

echo "2: Converting to singularity"
docker run -v /var/run/docker.sock:/var/run/docker.sock \
    -v $(pwd):/output \
    --privileged  -t \
    --rm quay.io/singularity/docker2singularity:v3.9.0 extract

mv extract*.sif extract.sif
