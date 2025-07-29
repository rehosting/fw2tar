#!/usr/bin/env python3
"""
Extract tar archive contents to JSON format for comparison.
This script reads a tar file and outputs a structured JSON representation
of its contents that can be compared programmatically.
"""

import tarfile
import json
import sys
import argparse
from pathlib import Path
from typing import Dict, Any


def extract_tar_to_json(tar_path: str) -> Dict[str, Any]:
    """Extract tar file contents to a JSON-serializable dictionary."""
    contents = {}

    try:
        with tarfile.open(tar_path, 'r:*') as tar:
            for member in tar.getmembers():
                # Create a clean path (normalize)
                path = member.name.lstrip('./')

                # Basic file info that's consistent across tar formats
                file_info = {
                    'type': 'unknown',
                    'size': member.size,
                    'mode': oct(member.mode),
                    'uid': member.uid,
                    'gid': member.gid,
                    'mtime': member.mtime
                }

                # Determine file type
                if member.isfile():
                    file_info['type'] = 'file'
                elif member.isdir():
                    file_info['type'] = 'directory'
                elif member.issym():
                    file_info['type'] = 'symlink'
                    file_info['linkname'] = member.linkname
                elif member.islnk():
                    file_info['type'] = 'hardlink'
                    file_info['linkname'] = member.linkname
                elif member.ischr():
                    file_info['type'] = 'chardev'
                    file_info['devmajor'] = member.devmajor
                    file_info['devminor'] = member.devminor
                elif member.isblk():
                    file_info['type'] = 'blockdev'
                    file_info['devmajor'] = member.devmajor
                    file_info['devminor'] = member.devminor
                elif member.isfifo():
                    file_info['type'] = 'fifo'

                contents[path] = file_info

    except Exception as e:
        print(f"Error reading tar file {tar_path}: {e}", file=sys.stderr)
        return {}

    return contents


def main():
    parser = argparse.ArgumentParser(
        description='Extract tar archive contents to JSON format')
    parser.add_argument('tar_file', help='Path to the tar file')
    parser.add_argument('-o', '--output', help='Output JSON file (default: stdout)')
    parser.add_argument('--pretty', action='store_true',
                       help='Pretty-print JSON output')

    args = parser.parse_args()

    # Check if tar file exists
    if not Path(args.tar_file).exists():
        print(f"Error: Tar file {args.tar_file} does not exist",
              file=sys.stderr)
        sys.exit(1)

    # Extract contents
    contents = extract_tar_to_json(args.tar_file)

    # Format JSON output
    if args.pretty:
        json_output = json.dumps(contents, indent=2, sort_keys=True)
    else:
        json_output = json.dumps(contents, sort_keys=True)

    # Write output
    if args.output:
        with open(args.output, 'w') as f:
            f.write(json_output)
        print(f"JSON output written to {args.output}")
    else:
        print(json_output)


if __name__ == '__main__':
    main()
