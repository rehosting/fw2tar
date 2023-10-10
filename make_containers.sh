#!/bin/bash

echo "1: Building docker container"
docker build -t myunblob .

echo "2: Converting to singularity"
docker run -v /var/run/docker.sock:/var/run/docker.sock -v $(pwd):/output --privileged -t --rm quay.io/singularity/docker2singularity myunblob
