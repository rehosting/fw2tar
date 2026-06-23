#!/usr/bin/env python3
"""Print the fw2tar manifest embedded in an output .rootfs.tar.gz.

The manifest is a versioned JSON spec tacked onto the gzip stream after the tar
EOF blocks (see src/archive.rs::write_manifest_trailer). Layout, in the
decompressed view, written last in the stream:

    [ manifest JSON bytes ]
    [ u32 little-endian: len(JSON) ]
    [ u16 little-endian: trailer-frame version ]
    [ 16-byte magic b"made with fw2tar" ]
"""
import argparse
import gzip
import json
import struct

MAGIC = b"made with fw2tar"


def read_manifest(firmware):
    """Decompress `firmware` and parse the manifest from the tail."""
    with gzip.open(firmware, "rb") as f:
        data = f.read()

    if len(data) < len(MAGIC) + 6 or data[-len(MAGIC):] != MAGIC:
        raise ValueError("no fw2tar manifest trailer found")

    rest = data[: -len(MAGIC)]
    (frame_version,) = struct.unpack("<H", rest[-2:])
    (json_len,) = struct.unpack("<I", rest[-6:-2])
    json_bytes = rest[-6 - json_len : -6]
    return frame_version, json.loads(json_bytes)


def main(firmware):
    _frame_version, manifest = read_manifest(firmware)

    print("Made with fw2tar")
    print(f"  Manifest version: {manifest.get('version')}")
    print(f"  File: {manifest.get('file')}")
    print(f"  Generated with command: {manifest.get('fw2tar_command')}")
    print(f"  Input file SHA1: {manifest.get('input_hash')}")
    print(f"  Extractor: {manifest.get('extractor')}")
    devices = manifest.get("devices", [])
    print(f"  Stripped device nodes: {len(devices)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show metadata from an fw2tar output archive")
    parser.add_argument("firmware", type=str, help="Output .rootfs.tar.gz from fw2tar")

    args = parser.parse_args()

    main(args.firmware)
