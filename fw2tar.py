import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from multiprocessing import Process, Lock, Manager
from pathlib import Path

EXTRACTORS=["unblob", "binwalk"]

def find_linux_filesystems(start_dir):
    key_dirs = {'bin', 'etc', 'lib', 'usr', 'var'}
    critical_files = {'bin/sh', 'etc/passwd'}
    min_required = (len(key_dirs) + len(critical_files)) // 2  # Minimum number of key dirs and files

    filesystems = defaultdict(lambda: {'score': 0, 'size': 0, 'path': '', 'nfiles': 0})

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

            # How many files are within this directory (recursively)?
            try:
                nfiles = sum(1 for _ in root_path.rglob('*'))
            except FileNotFoundError:
                nfiles = 0

            fs_key = str(root_path)
            filesystems[fs_key]['score'] = total_matches  # Use total matches as score
            filesystems[fs_key]['size'] = size  # Sum sizes of files for this filesystem
            filesystems[fs_key]['nfiles'] = nfiles
            filesystems[fs_key]['path'] = fs_key

    # Rank by size primarily, then score (total matches) as a tiebreaker
    ranked_filesystems = sorted(filesystems.values(), key=lambda x: (-x['size'], -x['score']))
    return [(Path(fs['path']), fs['size'], fs['nfiles']) for fs in ranked_filesystems]

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
    if result.returncode != 0:
        print(f"Error archiving root filesystem {rootfs_dir}: {result.stderr}")

def _extract(extractor, infile, extract_dir, log_file):
    if extractor == "unblob":
        cmd = subprocess.run(["unblob",
                                "--log", log_file,
                                "--extract-dir", str(extract_dir),
                                infile], check=True,
                                capture_output=True, text=True)
    elif extractor == "binwalk":
        cmd = subprocess.run(["binwalk", "--run-as=root",
                                "--preserve-symlinks",
                                "-eM",
                                "--log", log_file,
                                "-q", infile,
                                "-C", str(extract_dir)],
                                check=True, capture_output=True,
                                text=True)
    else:
        raise ValueError(f"Unknown extractor: {extractor}")
    
    if  cmd.returncode != 0:
        print(f"Extractor {extractor} exited non-zero: {cmd.returncode}\n\t{cmd.stderr}")

def extract_and_process(extractor, infile, outfile_base, scratch_dir, start_time, results, results_lock):
    with tempfile.TemporaryDirectory(dir=scratch_dir) as extract_dir:
        log_file = f"{outfile_base}.{extractor}.log"
        # Running the appropriate extractor
        _extract(extractor, infile, Path(extract_dir), log_file)
        post_extract = time.time()
        print(f"{extractor} complete after {post_extract - start_time:.2f}s")

        rootfs_choices = find_linux_filesystems(extract_dir)

        for idx, (root, size, nfiles) in enumerate(rootfs_choices):
            outfile = f"{outfile_base}.{extractor}.{idx}.tar.gz"
            _tar_fs(root, outfile)
            post_tar = time.time()
            #print(f"{extractor} filesystem {idx} archived after {post_tar - post_extract:.2f}s")

            with results_lock:
                results.append((extractor, idx, size, nfiles))

def main(infile, outfile_base, scratch_dir, extractors=None):
    # Launching both extraction processes in parallel
    processes = []
    manager = Manager()
    results = manager.list()
    results_lock = Lock()
    start_time = time.time()

    for extractor in extractors:
        print(f"Starting {extractor} extraction...")
        p = Process(target=extract_and_process, args=(extractor, infile, outfile_base,
                                                      scratch_dir, start_time,
                                                      results, results_lock))
        processes.append(p)
        p.start()

    # Wait for both processes to complete
    for p in processes:
        p.join()

    with results_lock:
        print(f"fw2tar extracted {len(results)} filesystems:")
        for (extractor, idx, size, nfiles) in sorted(results, key=lambda x: x[2], reverse=True):
            print(f"\t{extractor} output #{idx}: {nfiles:,} files, {size:,} bytes")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py [--extractors=EXTRACTORS] INFILE [OUTFILE_BASE] [SCRATCHDIR]")
        print("\tEXTRACTORS: comma-separated list of extractors (unblob, binwalk)")
        sys.exit(1)

    if "--extractors=" in sys.argv[1]:
        new_extractors = sys.argv[1].split("=")[1].split(",")
        if any([new_ext not in EXTRACTORS for new_ext in new_extractors]):
            raise ValueError(f"Unknown extractor: {new_extractors}. Supported extractors are: {EXTRACTORS}")
        extractors = sys.argv[1].split("=")[1].split(",")
        sys.argv.pop(1)

        if not len(extractors):
            raise ValueError("No extractors specified")
    else:
        extractors = EXTRACTORS

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

    print(f"Extracting {infile} using {' '.join(extractors)} extractors...")
    main(infile, outfile, scratch_dir, extractors=extractors)