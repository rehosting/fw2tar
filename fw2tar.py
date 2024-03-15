import argparse
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
                'cpio-root', 'squashfs-root', '0.tar'] # squashfs-root-* is special cased below

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
        # Check if the name ends with squashfs-root-*
        if entry.name.startswith('squashfs-root-') or entry.name.startswith("cpio-root-"):
            # Special case of bad suffixes
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
                    print(f"Warning {extractor if extractor else ''}: {executables} executables < {min_executables} required on analysis of FS {root_path} with size {size:,}")
                continue

            filesystems[str(root_path)].update({'score': total_matches, 'size': size, 'nfiles': nfiles, 'path': str(root_path), 'executables': executables})
        #elif total_matches > 0 and verbose:
            #print(f"{extractor if extractor else ''} found {total_matches} matches in {root_path} but not enough for a filesystem")

    # Filesystems will only have values if they met the minimum requirements
    # Now we rank by highest # executables with size and then score as tie breakers
    ranked_filesystems = sorted(filesystems.values(), key=lambda x: (-x['executables'], -x['size'], -x['score']))

    if verbose:
        for fs in ranked_filesystems:
            print(f"{extractor if extractor else ''} found filesystem: {fs['path']} with {fs['nfiles']:,} files, {fs['size']:,} bytes, {fs['executables']} executables")

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

    # We want the root directory: ./ to have consistent permissions
    # we'll just set it to 755. NOT RECURSIVE
    os.chmod(rootfs_dir, 0o755)

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
            if verbose:
                print(f"Archiving {root} as {tarbase}.tar.gz")
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

def monitor_processes(processes, results, max_wait=600, follow_up_wait=120):
    '''
    We'll wait up to max_wait for *any* result. After we have a result, we'll only wait
    up to follow_up_wait for all processes to complete. If they don't, we'll terminate them.
    '''

    start_time = time.time()
    while True:
        if (time.time() - start_time) > max_wait or (results and (time.time() - start_time) > follow_up_wait):
            for p in processes:
                if p.is_alive():
                    print(f"Terminating {p.name}...")
                    p.terminate()
            break
        if all(not p.is_alive() for p in processes):
            # All processes completed within the time frame
            break
        time.sleep(5)

def main(infile, outfile_base, scratch_dir="/tmp", extractors=None, verbose=False, primary_limit=1, secondary_limit=0):
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
        p.name = f"{extractor} extraction"
        processes.append(p)
        p.start()

    # Wait for both processes to complete
    monitor_processes(processes, results)

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

    col_names = ['permissions', 'ownership', 'size', 'date', 'time', 'path', 'issymlink', 'symlinkdest']

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
            # Results are distinct, but exist. Figure out why
            msg = "distinct_hash"
            paths = [f"{outfile_base}.{extractor}.0.tar.gz" for extractor in best_hashes.keys()]
            # Run tar tvf on each path - check if total number of lines is different -> different number of files
            # or check if columns are different: permissions, owner, group, size, date
            # Record the type of difference
            tar_result = {}
            for path in paths:
                tar_result[path] = subprocess.check_output(["tar", "tvf", path]).decode("utf-8", errors="ignore").splitlines()

            # First check: are line counts different?
            line_counts = {path: len(tar_result[path]) for path in paths}
            if len(set(line_counts.values())) > 1:
                # Which extractor has more files? - we'll take the best one
                best_extractor = max(line_counts, key=line_counts.get).split('.')[-4]
                if verbose:
                    print(f"Distinct file counts: best extractor is", best_extractor)
                msg = f"distinct_file_count_{best_extractor}"
            else:
                # Line counts are the same. Now check if the columns are different - we're going to take the unblob extraction
                # at this point because we like it better for symlinks/perms
                # We'll compare the first 100 lines of each tar tvf output
                # If we find a difference, we'll record it and break
                deltas = {k: False for k in col_names}
                for i, col_type in enumerate(col_names):
                    col_vals = {} # path -> col values

                    for path, data in tar_result.items():
                        col_vals[path] = [
                                x.split()[i] for x in data if len(x.split()) > i
                            ]

                    # Are any cols different - if so, break - note we might not care as much about earlier differences
                    # but we'll break on the first one we find.
                    if len(set([tuple(col_vals[path]) for path in col_vals])) > 1:
                        deltas[col_type] = True

                if any(deltas.values()):
                    msg = "distinct_" + "_".join([k for k, v in deltas.items() if v])

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
    if os.geteuid() != 0:
        print("This script must be run as (fake)root")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Process some files.")
    parser.add_argument("infile", type=str, help="Input file")
    parser.add_argument("outfile", nargs='?', type=str, help="Output file base (optional). Default is infile without extension.")
    parser.add_argument("scratch_dir", nargs='?', default="/tmp/", type=str, help="Scratch directory (optional). Default /tmp")
    parser.add_argument("--extractors", type=str, help=f"Comma-separated list of extractors. Supported values are {' '.join(EXTRACTORS)}", default="default_extractor")
    parser.add_argument("--verbose", action='store_true', help="Enable verbose output")
    parser.add_argument("--primary_limit", type=int, default=1, help="Maximum number of root-like filesystems to extract. Default 1")
    parser.add_argument("--secondary_limit", type=int, default=0, help="Maximum number of non-root-like filesystems to extract. Default 0")
    parser.add_argument("--force", action='store_true', help="Overwrite existing output file")

    args = parser.parse_args()

    if args.extractors == "default_extractor":
        args.extractors = EXTRACTORS
    else:
        args.extractors = args.extractors.split(',')

    if not args.outfile:
        # Filename without extension by default
        args.outfile = f"{Path(args.infile).parent}/{Path(args.infile).stem}"

    # Does outfile already exist?
    if Path(args.outfile + ".rootfs.tar.gz").exists():
        print(f"Output file {args.outfile}.rootfs.tar.gz already exists. " + ("Refusing to replace as --force not specified." if not args.force else "Overwriting."))
        if not args.force:
            sys.exit(1)
        else:
            os.remove(args.outfile + ".rootfs.tar.gz")

    main(args.infile, args.outfile, scratch_dir=args.scratch_dir,
         extractors=args.extractors, verbose=args.verbose,
         primary_limit=args.primary_limit, secondary_limit=args.secondary_limit)
