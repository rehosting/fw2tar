Permissions test
---
# Overview

Test our ability to maintain permissions through extraction by creating an archive with all permissions, extracting, then checking results.


# Usage

Run `make_files.sh` to generate `fs/` and `fs.tar.gz` with files in every set of permissions.
Go to the project root and run `./fw2tar.sh tests/fs.tar.gz`
Go back to this directory and run `python3 check.py fs.tar.binwalk.0.tar.gz` and `python3 check.py fs.tar.unblob.0.tar.gz`
