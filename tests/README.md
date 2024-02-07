Tests
---


Run `make_files.sh` to generate `fs/` and `fs.tar.gz` with files in every set of permissions.

Go to the project root and run `./run.sh tests/fs.tar.gz`

Go back to this directory and run `python3 check.py fs.tar.binwalk.tar.gz` and `python3 check.py fs.tar.unblob.tar.gz`
