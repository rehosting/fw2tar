#!/bin/bash

rm -f unblob*.sif

echo "1: Building docker container"
docker build -t unblob .

echo "2: Converting to singularity"
docker run -v /var/run/docker.sock:/var/run/docker.sock -v $(pwd):/output --privileged -t --rm quay.io/singularity/docker2singularity unblob

mv unblob*.sif unblob.sif
