# fw2tar: Firmware to Root Filesystem Tarball Converter

`fw2tar` is an _unprivileged_ utility designed to seamlessly convert firmware images
into compressed tar archives, accurately reflecting the root filesystem's original permissions.


## Overview
Converting firmware images into accessible filesystems often presents a significant challenge:
striking the right balance between maintaining security and ensuring accurate extraction.
Traditional methods require elevated privileges to replicate the filesystem accurately,
risking security when processing untrusted inputs. `fw2tar` takes a different approach by
producing an archive of the target filesystem which faithfully preserves filesystem
permissions without the need to run any utilities as root.

`fw2tar` first extracts firmware images using both [unblob](https://github.com/onekey-sec/unblob/) and [binwalk](https://github.com/ReFirmLabs/binwalk),
finds root filesystems within the extracted directories, and packs each into its own archive while preserving file permissions.

Preserving permissions is vital for [firmware rehosting](https://dspace.mit.edu/handle/1721.1/130505)
where altering file ownership or permissions could undermine the integrity of an analysis.

## Key Features

- **Unprivileged Extraction**: Runs with standard user privileges in an unpriviliged docker or singularity container.
- **Permission Preservation**: Maintains correct filesystem permissions, facilitating accurate dynamic analysis.
- **Root Filesystem Extraction**: Instead of producing every extracted file, `fw2tar` identifies and outputs archives for each identified (Linux) root filesystem.
- **Multiple Extractors**: Filesystems can be extracted using `unblob`, `binwalk`, or both.

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
    fakeroot python3 /fw2tar.py /host/$(basename $INPUT_FILE)
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
    fakeroot python3 /fw2tar.py /host/$(basename $INPUT_FILE)
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
