import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from multiprocessing import Process
from pathlib import Path

# Config options
UNBLOB = True
BINWALK = True

def find_linux_filesystems(start_dir):
    key_dirs = {'bin', 'etc', 'lib', 'usr', 'var'}
    critical_files = {'bin/sh', 'etc/passwd'}
    min_required = (len(key_dirs) + len(critical_files)) // 2  # Minimum number of key dirs and files

    filesystems = defaultdict(lambda: {'score': 0, 'size': 0, 'path': ''})

    for root, dirs, files in os.walk(start_dir):
        root_path = Path(root)
        depth = len(root_path.relative_to(start_dir).parts)

        # Directly check for presence of key directories and critical files
        matched_dirs = key_dirs.intersection(set(dirs))
        matched_files = set()
        for critical_file in critical_files:
            if (root_path / critical_file.split('/')[-1]).exists():
                matched_files.add(critical_file)

        total_matches = len(matched_dirs) + len(matched_files)
        if total_matches >= min_required:
            try:
                size = sum((root_path / file).stat().st_size for file in files)
            except FileNotFoundError:
                continue
            fs_key = str(root_path)

            filesystems[fs_key]['score'] = total_matches  # Use total matches as score
            filesystems[fs_key]['size'] += size  # Sum sizes of files for this filesystem
            filesystems[fs_key]['path'] = fs_key

    # Rank by size primarily, then score (total matches) as a tiebreaker
    ranked_filesystems = sorted(filesystems.values(), key=lambda x: (-x['size'], -x['score']))
    return [Path(fs['path']) for fs in ranked_filesystems]

def _tar_fs(rootfs_dir, outfile):
    # Constructing the tar command with exclusions
    tar_command = [
        "tar",
        "czf", outfile,
        "--xattrs",
        "--exclude=*_extract",
        "--exclude=./dev",
        "-C", str(rootfs_dir),
        '.'
    ]

    # Execute the tar command
    result = subprocess.run(tar_command, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Root filesystem {rootfs_dir} archived to {outfile}")
    else:
        print(f"Error archiving root filesystem {rootfs_dir}: {result.stderr}")

def _extract(extractor, infile, extract_dir, log_file):
        if extractor == "unblob":
            subprocess.run(["unblob",
                            "--log", log_file,
                            "--extract-dir", str(extract_dir),
                            infile], check=True)
        elif extractor == "binwalk":
            subprocess.run(["binwalk", "--run-as=root",
                            "--preserve-symlinks",
                            "-eM",
                            "--log", log_file,
                            "-q", infile,
                            "-C", str(extract_dir)],
                            check=True)
        else:
            raise ValueError(f"Unknown extractor: {extractor}")

def extract_and_process(extractor, infile, outfile_base, scratch_dir):
    with tempfile.TemporaryDirectory(dir=scratch_dir) as extract_dir:
        log_file = f"{outfile_base}.{extractor}.log"

        # Running the appropriate extractor
        _extract(extractor, infile, Path(extract_dir), log_file)

        rootfs_choices = find_linux_filesystems(extract_dir)
        print("Rootfs choices: ", rootfs_choices)

        for idx, root in enumerate(rootfs_choices):
            outfile = f"{outfile_base}.{extractor}.{idx}.tar.gz"
            _tar_fs(root, outfile)


def main(infile, outfile_base, scratch_dir):

    # Launching both extraction processes in parallel
    processes = []
    if UNBLOB:
        p_unblob = Process(target=extract_and_process, args=("unblob", infile, outfile_base, scratch_dir))
        processes.append(p_unblob)

    if BINWALK:
        p_binwalk = Process(target=extract_and_process, args=("binwalk", infile, outfile_base, scratch_dir))
        processes.append(p_binwalk)

    # Start the processes
    for p in processes:
        p.start()

    # Wait for both processes to complete
    for p in processes:
        p.join()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py INFILE [OUTFILE_BASE] [SCRATCHDIR]")
        sys.exit(1)

    infile = sys.argv[1]

    if len(sys.argv) < 3:
        # Filename without extension by default
        outfile = f"{Path(infile).parent}/{Path(infile).stem}"
    else:
        outfile = sys.argv[2]

    if len(sys.argv) < 4:
        # Default to /tmp
        scratch_dir = "/tmp/"
    else:
        scratch_dir = sys.argv[3]

    main(infile, outfile, scratch_dir)
