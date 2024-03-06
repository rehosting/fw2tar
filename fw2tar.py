import os
import stat
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from multiprocessing import Process, Lock, Manager
from pathlib import Path

EXTRACTORS=["unblob", "binwalk"]

BAD_SUFFIXES = ['_extract', '.uncompressed', '.unknown', # Filename suffixes that show up as extraction artifacts
                'squashfs-root', '0.tar']

def get_dir_size_exes(path):
    '''
    Recursively calculate the size of a directory. Ignore our disallowed suffixes.
    as those are extraction artifacts.
    '''
    total_size, total_files, total_executables = 0, 0, 0

    for entry in path.iterdir():
        if any([entry.name.endswith(x) for x in BAD_SUFFIXES]):
            # Don't recurse into nor count files with bad suffixes
            continue

        if entry.is_file():
            total_files += 1
            if entry.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                total_executables += 1
            try:
                total_size += entry.stat().st_size
            except FileNotFoundError as e:
                print(f"Unexpected FileNotFoundError: {e}")
                continue
            except OSError as e:
                # Happens if there are too many symlinks
                print("Unexpected OSError: {e}")
                continue
            except PermissionError as e:
                # Happens if we can't read the file
                print("Unexpected PermissionError: {e}")
                continue
            except Exception as e:
                print(f"Unexpected error: {e}")
                continue

        elif entry.is_dir() and not entry.is_symlink():
            # We can't recurse into symlink directories because they could
            # take us out of the extract dir or make us go into a cycle.
            # But we do count them as files above because symlinks should
            # count as executables, i.e., /bin/sh -> /bin/bash
            (dir_sz, dir_files, dir_exe) = get_dir_size_exes(entry)
            total_size += dir_sz
            total_files += dir_files
            total_executables += dir_exe

    return (total_size, total_files, total_executables)

def count_executable_files(path):
    """Count executable files in directory."""
    count = 0
    for entry in path.rglob('*'):
        if entry.is_file():
            mode = entry.stat().st_mode
            if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                count += 1
    return count

def find_linux_filesystems(start_dir, min_executables=10, extractor=None, verbose=False):
    key_dirs = {'bin', 'etc', 'lib', 'usr', 'var'}
    critical_files = {'bin/sh', 'etc/passwd'}
    min_required = (len(key_dirs) + len(critical_files)) // 2  # Minimum number of key dirs and files

    filesystems = defaultdict(lambda: {'score': 0, 'size': 0, 'path': '', 'nfiles': 0, 'executables': 0})

    for root, dirs, files in os.walk(start_dir):
        root_path = Path(root)

        matched_dirs = key_dirs.intersection(set(dirs))
        matched_files = set()
        for critical_file in critical_files:
            if (root_path / critical_file.split('/')[-1]).exists():
                matched_files.add(critical_file)

        total_matches = len(matched_dirs) + len(matched_files)
        if total_matches >= min_required:
            size, nfiles, executables = get_dir_size_exes(root_path)

            if executables < min_executables:
                if verbose:
                    print(f"Warning {extractor if extractor else ''}: {executables} executables < {min_executables} required on analysis of FS {root_path}")
                continue

            filesystems[str(root_path)].update({'score': total_matches, 'size': size, 'nfiles': nfiles, 'path': str(root_path), 'executables': executables})

    # Filter filesystems by those having at least min_executables, then rank by size, executables, and score
    #filtered_filesystems = {k: v for k, v in filesystems.items() if v['executables'] >= min_executables}
    filtered_filesystems = {k: v for k, v in filesystems.items()}
    ranked_filesystems = sorted(filtered_filesystems.values(), key=lambda x: (-x['size'], -x['executables'], -x['score']))

    return [(Path(fs['path']), fs['size'], fs['nfiles']) for fs in ranked_filesystems]

def find_all_filesystems(start_dir, other_than=None):
    '''
    Find every non-empty extracted filesystem, except those in other_than list.
    Returns a sorted list based on number of files (descending order).
    '''
    # Initialize filesystems with defaultdict
    filesystems = defaultdict(lambda: {'size': 0, 'nfiles': 0, 'path': ''})

    for root, dirs, files in os.walk(start_dir):
        root_path = Path(root)

        # Check if the current directory is not in the exclusion list and ends with '_extract'
        if root_path.name.endswith('_extract') and root_path not in (other_than or []):
            size, nfiles, _ = get_dir_size_exes(root_path) # Don't care about executables
            if size == 0:
                continue
            filesystems[str(root_path)].update({'size': size, 'nfiles': nfiles, 'path': str(root_path)})
            print(f"Found non-root filesystem: {root_path} with {nfiles:,} files, {size:,} bytes")

    # Convert the filesystems to a list of tuples and sort them by the number of files
    sorted_filesystems = sorted(filesystems.values(), key=lambda x: x['nfiles'], reverse=True)

    # Return list of tuples (Path, size, nfiles) for each filesystem
    return [(Path(fs['path']), fs['size'], fs['nfiles']) for fs in sorted_filesystems]


def _tar_fs(rootfs_dir, tarbase):
    # First, define the name of the uncompressed tar archive (temporary name)
    uncompressed_outfile = tarbase + '.tar'
    tar_command = [
        "tar",
        "-cf",
        uncompressed_outfile,
        "--sort=name",
        "--mtime=UTC 2019-01-01",

        #"--xattrs", # Introduces non-determinism

        # Common binwalk artifacts:
            "--exclude=0.tar",
            "--exclude=squashfs-root",

        # Unblob artifacts
            "--exclude=*_extract",
            "--exclude=*.uncompressed",
            "--exclude=*.unknown",

        # Don't want to take devices, permissions are a pain and tar complains about character devices
            "--exclude=./dev",

        "-C", str(rootfs_dir),
        "."
    ]

    # Execute the tar command
    tar_result = subprocess.run(tar_command, capture_output=True, text=True)
    if tar_result.returncode != 0:
        print(f"Error archiving root filesystem {rootfs_dir}: {tar_result.stderr}")
        return

    # Now, compress the tar archive using gzip with the --no-name option
    # Output filename will be tarbase.tar.gz
    gzip_command = ["gzip", "--no-name", "-f", uncompressed_outfile]

    # Execute the gzip command
    gzip_result = subprocess.run(gzip_command, capture_output=True, text=True)
    if gzip_result.returncode != 0:
        print(f"Error compressing tar archive {uncompressed_outfile}: {gzip_result.stderr}")
        return

    # Chmod the tar.gz file to 644
    os.chmod(f"{uncompressed_outfile}.gz", 0o644)

def _extract(extractor, infile, extract_dir, log_file):
    try:
        if extractor == "unblob":
            subprocess.run(["unblob",
                            "--log", log_file,
                            "--extract-dir", str(extract_dir),
                            infile], check=True,
                            capture_output=True, text=True)
        elif extractor == "binwalk":
            subprocess.run(["binwalk", "--run-as=root",
                            "--preserve-symlinks",
                            "-eM",
                            "--log", log_file,
                            "-q", infile,
                            "-C", str(extract_dir)],
                            check=True, capture_output=True,
                            text=True)
        else:
            raise ValueError(f"Unknown extractor: {extractor}")

    except subprocess.CalledProcessError as e:
        print(f"Error running {extractor}: {e.returncode}\nstdout: {e.stdout}\nstderr: {e.stderr}")
        raise e

def extract_and_process(extractor, infile, outfile_base, scratch_dir, start_time, verbose, primary_limit,
                        secondary_limit, results, results_lock):
    with tempfile.TemporaryDirectory(dir=scratch_dir) as extract_dir:
        log_file = f"{outfile_base}.{extractor}.log"
        # Running the appropriate extractor
        _extract(extractor, infile, Path(extract_dir), log_file)
        post_extract = time.time()
        if verbose:
            print(f"{extractor} complete after {post_extract - start_time:.2f}s")

        rootfs_choices = find_linux_filesystems(extract_dir, extractor=extractor, verbose=verbose)

        if not len(rootfs_choices) and verbose:
            print(f"No Linux filesystems found extracting {infile} with {extractor}")
            return

        for idx, (root, size, nfiles) in enumerate(rootfs_choices):
            if idx >= primary_limit:
                break
            tarbase = f"{outfile_base}.{extractor}.{idx}"
            _tar_fs(root, tarbase)

            archive_hash = subprocess.run(["sha1sum", f"{tarbase}.tar.gz"], capture_output=True, text=True).stdout.split()[0]
            with results_lock:
                results.append((extractor, idx, size, nfiles, True, archive_hash))

        if secondary_limit > 0:
            # Now find non-Linux filesystems, anything except rootfs_choices
            other_filesystems = find_all_filesystems(extract_dir, other_than=[x[0] for x in rootfs_choices])

            if verbose:
                print(f"Found {len(other_filesystems)} non-root filesystems")

            for idx, (root, size, nfiles) in enumerate(other_filesystems):
                if idx >= secondary_limit:
                    break
                tarbase = f"{outfile_base}.{extractor}.nonroot.{idx}"
                _tar_fs(root, tarbase)

                with results_lock:
                    results.append((extractor, idx, size, nfiles, False))

def main(infile, outfile_base, scratch_dir, extractors=None, verbose=False, primary_limit=1, secondary_limit=0):
    # Launching both extraction processes in parallel
    processes = []
    manager = Manager()
    results = manager.list()
    results_lock = Lock()
    start_time = time.time()

    for extractor in extractors:
        if verbose:
            print(f"Starting {extractor} extraction...")
        p = Process(target=extract_and_process, args=(extractor, infile, outfile_base,
                                                      scratch_dir, start_time, verbose,
                                                      primary_limit, secondary_limit,
                                                      results, results_lock))
        processes.append(p)
        p.start()

    # Wait for both processes to complete
    for p in processes:
        p.join()
    # Note we no longer need results_lock because we're back to a single process

    best_hashes = {} # extractor -> hash of best filesystem
    for (extractor, idx, size, nfiles, root, archive_hash) in results:
        if idx == 0:
            best_hashes[extractor] = archive_hash

    if verbose:
        for (extractor, idx, size, nfiles, root, archive_hash) in sorted(results, key=lambda x: (x[4], x[2]), reverse=True):
            if root and idx == 0:
                best_hashes[extractor] = archive_hash
            if verbose:
                if root:
                    print(f"\t{archive_hash}: {extractor: <10} primary #{idx}: {nfiles:,} files, {size:,} bytes.")
                else:
                    print(f"\t{archive_hash}: {extractor} secondary #{idx}: {nfiles:,} files, {size:,} bytes")

    # Compare results, if we only have one, take it. Otherwise prioritize unblob.
    # Avoid storing duplicates of identical filesystem.
    # Store best results at {input_base}.rootfs.tar.gz, others at {input_base}.{extractor}.0.tar.gz

    best_extractor = None
    if len(best_hashes) == 0:
        # No extractors found anything
        msg = "nofs"
    elif len(best_hashes) == 1:
        # Only one extractor worked
        best_extractor = list(best_hashes.keys())[0]
        msg = f"only_{best_extractor}"
    else:
        # Multiple extractors found something
        best_extractor = "unblob"
        if len(set(best_hashes.values())) == 1:
            msg = "identical"
        else:
            # Results are distinct, but exist. Warn and take unblob
            msg = "distinct"

    # Report results, even if non-verbose
    print(f"Best extractor: {best_extractor} ({msg})" + (f" archive at {os.path.basename(outfile_base)}.rootfs.tar.gz" if best_extractor else ""))

    # Write msg into a file. Only if we have multiple extractors - if we just have one the results is either output exists/no output exists
    if len(extractors) > 1:
        with open(f"{outfile_base}.txt", "w") as f:
            f.write(msg+"\n")

    # If we have a best_extractor, we can rename the file and delete the others
    if best_extractor:
        best_filename = f"{outfile_base}.{best_extractor}.0.tar.gz"
        os.rename(best_filename, f"{outfile_base}.rootfs.tar.gz")

        # If filesystems were identical we can delete the others. Otherwise we'll leave them for the user to inspect
        if msg == "identical":
            for other_extractor in best_hashes.keys():
                if other_extractor != best_extractor:
                    os.remove(f"{outfile_base}.{other_extractor}.0.tar.gz")

if __name__ == "__main__":
    os.umask(0o000)
    # Assert root
    if os.geteuid() != 0:
        print("This script must be run as (fake)root")
        sys.exit(1)

    # TODO: expose as CLI options
    verbose = False
    primary_limit = 1
    secondary_limit = 0

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

    if verbose:
        print(f"Extracting {infile} using {' '.join(extractors)} extractors...")
    main(infile, outfile, scratch_dir, extractors=extractors, verbose=verbose, primary_limit=primary_limit, secondary_limit=secondary_limit)