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

Once installed, repackaging a firmware is as simple as:

```
fw2tar /path/to/your/firmware.bin
```

Which will generate `/path/to/your/firmware.rootfs.tar.gz` containing the rootfs of the firmware.

There are two types of arguments, wrapper arguments (which handle anything outside of the fw2tar docker container, such as rebuilding the container or specifying a docker image tag) and fw2tar flags (which get passed to the actual application). These can be found with `--wrapper-help` and `--help` respectively.

### Installing Pre-built

#### Download the container
Ensure Docker is installed on your system, then download the container from GitHub:

```sh
docker pull rehosting/fw2tar:latest
```

### Install the Wrapper

Run the following command:

```
docker run rehosting/fw2tar
```

it will give you a command for installing system-wide or for your individual user. Run the command for your preferred install type, then follow any additional instructions from that command.

### Docker from source

Ensure you have Git and Docker installed, then:

#### Clone and build the container

```sh
git clone https://github.com/rehosting/fw2tar.git
cd fw2tar
./fw2tar --build
```

If you wish to install globally, see "Install the Wrapper" above.

#### Extract Firmware

Replace `/path/to/your/firmware.bin` with the actual path to your firmware file:

```sh
./fw2tar /path/to/your/firmware.bin
```

The resulting filesystem(s) will be output to `/path/to/your/firmware.{binwalk,unblob}.*.tar.gz`, with each root filesystem extracted to its own archive.

### Singularity

#### Build the Container

On a system where you have root permissions, clone this repository and
then build `fw2tar.sif` using `./build_singularity.sh`, or manually with:

```sh
docker build -t rehosting/fw2tar:latest .
docker run -v /var/run/docker.sock:/var/run/docker.sock \
    -v $(pwd):/output \
    --privileged -t \
    --rm quay.io/singularity/docker2singularity:v3.9.0 rehsoting/fw2tar
mv fw2tar*.sif fw2tar.sif
```

#### Run the Container

```sh
export INPUT_FILE=/path/to/your/firmware.bin
singularity exec \
    -B $(dirname $INPUT_FILE):/host \
    fw2tar.sif \
    fakeroot_fw2tar /host/$(basename $INPUT_FILE)
```

Your filesystem(s) will be output to `/path/to/your/firmware.{binwalk,unblob}.tar.gz`.

## Comparing Filesystem Archives

To compare filesystems generated with binwalk and unblob, use the `diff_archives.py`
 script included in the repository.
 This can help identify discrepancies and verify the accuracy of the extracted filesystems.

## Extractor Forks
To accomplish its goals, we maintain slightly-modified forks of both [unblob](https://github.com/onekey-sec/unblob/) and [binwalk](https://github.com/ReFirmLabs/binwalk).
- [unblob fork](https://github.com/rehosting/unblob): forked to preserve permissions and handle symlinks.
- [binwalk fork](https://github.com/rehosting/binwalk): forked to better support ubifs extraction.

We express our gratitude to the developers of these tools for their hard work that makes `fw2tar` possible.
