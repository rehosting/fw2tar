#!/usr/bin/env python3
import argparse
import gzip
import json

def main(firmware):
    with gzip.open(firmware, 'rb') as f:
        f.seek(-0x1000, 2)
        end_bytes = f.read()

    str_len = list(end_bytes[::-1]).index(0)
    string = end_bytes[-str_len:].decode()

    metadata, magic = string.split('\n')
    assert(magic == "made with fw2tar")

    metadata = json.loads(metadata)


    print("Made with fw2tar")
    print(f"  File: {metadata['file']}")
    print(f"  Generated with command: {metadata['fw2tar_command']}")
    print(f"  Input file SHA1: {metadata['input_hash']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show metadata from fw2tar output argive")
    parser.add_argument("firmware", type=str, help="Output .tar.gz from fw2tar")

    args = parser.parse_args()

    main(args.firmware)
