# fw2tar: Firmware to Root Filesystem Tarball Converter

`fw2tar` is a robust, _unprivileged_ utility designed to convert firmware images into compressed tar archives of the image's root filesystem, preserving correct permissions without requiring root access.

## Overview

Many standard filesystem extractors strugle with the trade-off between security and functionality where root privileges are required to maintain accurate filesystem permissions. This utility tackles this trade-off by leveraging `fakeroot` and `tar`, allowing extractors to operate unprivileged while still preserving correct permissions in the output archives. This feature is particularly crucial for dynamic analyses of firmware, such as [rehosting](https://dspace.mit.edu/handle/1721.1/130505), where maintaining accurate filesystem permissions is essential.

When dealing with images containing multiple root filesystems, `fw2tar` extracts each into its own archive, streamlining the process for users.

## Key Features

- **Unprivileged Extraction**: Runs with standard user privileges using `fakeroot`, enhancing security.
- **Permission Preservation**: Maintains correct filesystem permissions, facilitating accurate dynamic analysis.
- **Root Filesystem Extraction**: Instead of producing every extracted file, `fw2tar` identifies and outputs archives for each identified (Linux) root filesystem.
- **Multiple Extractors**: Extract filesystems with both [unblob](https://github.com/onekey-sec/unblob/) and [binwalk](https://github.com/ReFirmLabs/binwalk).

## Usage

### Pre-built docker container

#### Download the container
Ensure Docker is installed on your system, then download the container from GitHub:

```sh
docker pull ghcr.io/andrewfasano/fw2tar:main
```

#### Extract Firmware
Replace `/path/to/your/firmware.bin` with the actual path to your firmware file:

```sh
export INPUT_FILE=/path/to/your/firmware.bin
docker run --rm -it \
    -v $(dirname $INPUT_FILE):/host \
    ghcr.io/andrewfasano/fw2tar:main \
    /host/$(basename $INPUT_FILE)
```

The resulting filesystem(s) will be output to `/path/to/your/firmware.{binwalk,unblob}.*.tar.gz`, with each root filesystem extracted to its own archive.

### Docker from source
Ensure you have Git and Docker installed, then:

#### Clone and build the container
```sh
git clone https://github.com/AndrewFasano/fw2tar.git
docker build -t fw2tar fw2tar/
```

#### Extract Firmware
Replace `/path/to/your/firmware.bin` with the actual path to your firmware file:

```sh
./fw2tar/fw2tar.sh /path/to/your/firmware.bin
```

The resulting filesystem(s) will be output to `/path/to/your/firmware.{binwalk,unblob}.*.tar.gz`, with each root filesystem extracted to its own archive.

### Singularity

#### Build the Container

On a system where you have root permissions, clone this repository and
then build `fw2tar.sif` using `./build_singularity.sh`, or manually with:

```sh
docker build -t fw2tar .
docker run -v /var/run/docker.sock:/var/run/docker.sock \
    -v $(pwd):/output \
    --privileged -t \
    --rm quay.io/singularity/docker2singularity:v3.9.0 fw2tar
mv fw2tar*.sif fw2tar.sif
```

#### Run the Container

```sh
export INPUT_FILE=/path/to/your/firmware.bin
singularity exec \
    -B $(dirname $INPUT_FILE):/host \
    fw2tar.sif \
    python3 /fw2tar.py /host/$(basename $INPUT_FILE)
```

Your filesystem(s) will be output to `/path/to/your/firmware.{binwalk,unblob}.tar.gz`.

## Comparing Filesystem Archives

To compare filesystems generated with binwalk and unblob, use the `diff_archives.py`
 script included in the repository.
 This can help identify discrepancies and verify the accuracy of the extracted filesystems.

## Extractor Forks
To accomplish its goals, we maintain slightly-modified forks of both [unblob](https://github.com/onekey-sec/unblob/) and [binwalk](https://github.com/ReFirmLabs/binwalk).
- [unblob fork](https://github.com/andrewfasano/unblob): forked to preserve permissions and handle symlinks.
- [binwalk fork](https://github.com/andrewfasano/binwalk): forked to better support ubifs extraction.

We express our gratitude to the developers of these tools for their hard work that makes `fw2tar` possible.
