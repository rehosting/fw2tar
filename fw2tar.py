import argparse
import os
import stat
import subprocess
import sys
import re
import tempfile
import time
import tarfile
import shutil
from copy import deepcopy
from itertools import combinations, product
from collections import defaultdict
from multiprocessing import Process, Lock, Manager
from pathlib import Path

EXTRACTORS=["unblob", "binwalk"]

BAD_SUFFIXES = ['_extract', '.uncompressed', '.unknown', # Filename suffixes that show up as extraction artifacts
                'cpio-root', 'squashfs-root', '0.tar'] # squashfs-root-* is special cased below

BAD_MOUNTPOINTS  = ["tmp", "dev", "sys", "proc"]

def logical_resolve(path, source=None):
    """
    Resolve a path (in the form of a string or a pathlib.Path object) into its
    absolute form by logically handling '.' and '..' components.
    This function does not access the filesystem.

    If a source is provided, the path is resolved relative to the source path.
    """
    # Ensure path is a list of components if it's not already
    if isinstance(path, str):
        path = Path(path)
    parts = list(path.parts)

    if source is not None:
        # Ensure source is a list of components if it's not already
        if isinstance(source, str):
            source = Path(source)
        source_parts = list(source.parts)

        # Prepend the source path to the path to resolve
        parts = source_parts + parts

    resolved_parts = []
    for part in parts:
        if part == '..':
            if resolved_parts:
                resolved_parts.pop()  # Go up one directory level
        elif part not in ('', '.', '/'):
            resolved_parts.append(part)  # Add actual directory/file name

    # Reconstruct the path from the resolved parts
    resolved_path = Path(*resolved_parts)
    
    # Ensure the path is absolute
    if not path.is_absolute():
        resolved_path = Path("/") / resolved_path

    return resolved_path

def realize_fs(mount_points, file_map):
    '''
    Given a dictionary of mount points in the form {"./": "filename", "./mnt": "filename2", ...}
    and a dictionary of file_map in the form {"filename": {path -> member info}, ...},
    combine them as specified in mount_points to  produce a new fs dictionary
    '''
    mount_fs = {}
    for mount_point, infile in mount_points.items():
        assert(mount_point.endswith("/"))
        for path, detail in file_map[infile].items():
            mount_fs[mount_point + path[2:]] = detail
    return mount_fs


def calculate_score(mount_fs, report=False):
    '''
    Count the number of files and *resolveable* symlinks in mount_fs with its current layout
    '''

    total_files = len([name for name, member in mount_fs.items() if not member.issym()])
    dangling_link_targets = set()

    # Now let's look at each symlink
    for name, member in {name: member for name, member in mount_fs.items() if member.issym()}.items():
        # Can we resolve this symlink?
        target = member.linkname
        if target.startswith("/") or target.startswith("./"):
            target = str(logical_resolve(target))
        else:
            parent = name
            if parent.endswith("/"):
                parent = parent[:-1]
            parent = "/".join(parent.split("/")[:-1])
            target = str(logical_resolve(target, parent))
            #print("Relative symlink", name, raw_target, target)

        if target.startswith("/"):
            target = "." + target
        elif not target.startswith("./"):
            target = "./" + target


        if any(target.startswith("./" + x) for x in BAD_MOUNTPOINTS):
            continue

        if target not in mount_fs:
            dangling_link_targets.add(target)
            if report:
                print(f"Dangling link: {name} -> {target}")
            continue
        total_files += 1

    return total_files, dangling_link_targets

def sanitize_path(this_path, trailing_slash = False):
    '''
    Ensure path starts with ./
    If trailing_slash is True, ensure path ends with a /
    '''
    if trailing_slash and not this_path.endswith("/"):
        this_path += "/"

    if this_path.startswith("/"):
        this_path = "." + this_path
    elif not this_path.startswith("./"):
        this_path = "./" + this_path
    return this_path


def check_mountpoint(this_path, mount_points, realized_fs):
    if this_path in mount_points:
        return False

    # Check if this path exists in our filesystem - it must exist as an empty directory
    mountpoint = None
    if this_path not in realized_fs:
        if this_path.endswith("/"):
            this_path = this_path[:-1]
            if this_path in realized_fs:
                mountpoint = realized_fs[this_path]
    else:
        mountpoint = realized_fs[this_path]

    if not mountpoint:
        #print(f"Skipping {this_path} - not in realized_fs")
        return False

    # Is this a non-empty directory?
    if len([x for x in realized_fs if x.startswith(this_path+"/")]) > 1:
        return False

    if mountpoint.issym():
        # Follow the symlink
        new_path = sanitize_path(mountpoint.linkname)
        return check_mountpoint(new_path, mount_points, realized_fs)

    return True

def find_best_score(mount_points, file_map):
    if not len(mount_points):
        return mount_points, 0

    realized_fs = realize_fs(mount_points, file_map)

    best_score, dangling_link_targets = calculate_score(realized_fs)
    best_mount_points = mount_points
    found_improvement = False

    # Let's test each mount point to find the best (or none)
    # If we find a best, recurse (greedy), otherwise return
    # For each dangling link, try to resolve it using any unmounted fs
    for dangling_link in dangling_link_targets:
        for other_file in file_map:
            if other_file in mount_points.values():
                continue
            other_files = file_map[other_file]

            # For each parent directory of dangling_link, check if we could mount other_files there
            for idx in range(len(dangling_link.split("/"))-1):
                this_path = sanitize_path("/".join(dangling_link.split("/")[:idx+1]), trailing_slash=True)

                if not check_mountpoint(this_path, mount_points, realized_fs):
                    # Invalid mount point - has files, isn't present, etc
                    continue
                residual = sanitize_path("/".join(dangling_link.split("/")[idx+1:]))
                if residual in other_files:
                    # We can mount other_files at this_path to resolve dangling_link
                    # Let's calculate the new score
                    new_mount_points = deepcopy(mount_points)
                    new_mount_points[this_path] = other_file

                    new_fs = realize_fs(new_mount_points, file_map)
                    this_score, new_dangles = calculate_score(new_fs)

                    # One final filter - if we just added more files, but didn't actually resolve anything in particular we knew about, this is probably wrong
                    # Ensure we've removed at least one dangling link. But we could've added new dangles too
                    resolved = set()
                    for x in dangling_link_targets:
                        if x in new_fs:
                            resolved.add(x)

                    if not len(resolved):
                        continue

                    if this_score > best_score or (this_score == best_score and len(str(best_mount_points.keys())) > len(str(new_mount_points.keys()))):
                        # If we have a tie, select the shorter mount string i.e., higher in the FS
                        found_improvement = True
                        this_mount_points, this_score = find_best_score(new_mount_points, file_map)
                        if this_mount_points is not None:
                            best_mount_points = this_mount_points
                            best_score = this_score
                        

    if not found_improvement:
        # We didn't find a better mount point, so return
        return best_mount_points, best_score

    # Recurse
    return find_best_score(best_mount_points, file_map)

def is_valid_rootfs(scenario, file_map):
    # Given a scenario, ensure it's a valid rootfs
    # by checking for a set of required directories and files
    # and ensuring at least some executables are present

    key_dirs = {'bin', 'etc', 'lib', 'usr', 'var', 'tmp'}
    critical_files = {'bin/sh', 'etc/passwd'}
    min_required = 2 # Just find something
    found = 0

    for d in key_dirs:
        # First assume ./ -> file and look that up in file_map
        root = file_map[scenario['./']]
        if "./{d}/" in root or f"./{d}" in root:
            found += 1
        elif f"./{d}/" in scenario:
            found += 1

    # For each critical file, check if it's present - first check if dir is a symlink/mount
    for f in critical_files:
        d = f.split("/")[0]
        f = f.split("/")[1]

        if f"./{d}/{f}" in file_map[scenario['./']]:
            # Mounted in rootfs
            found += 1

        elif f"./{d}" in scenario:
            # Mounted dir - check other file_map
            if f"./{f}" in file_map[scenario[f"./{d}/"]]:
                found += 1
    return found >= min_required



def find_referenced_paths(tarnames):
    '''
    Look through every file in each tar archive to identify Linux paths with at least 2 slashes. 
    This function attempts to handle both text and binary files by looking for paths in the binary data.
    Paths are required to have at least 2 slashes to be considered.
    Returns a set of unique paths found across all specified tar archives.
    '''
 
    # Regex to match Linux paths with at least 2 slashes and excluding specific characters
    path_regex = re.compile(rb'/[^/\0\n<>"\'! :\?]+(?:/[^/\0\n<>()%"\'! ;:\?]+)+')

    # Pattern to detect if a match is immediately preceded by a common network protocol
    protocol_pattern = rb'(?:http|ftp|https)://'
    
    referenced_paths = set()

    for tarname in tarnames:
        with tarfile.open(tarname, "r:*") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    file_contents = tar.extractfile(member).read()
                    # Find all matches in the file, considering it as binary data
                    for match in re.findall(path_regex, file_contents):
                        # Check if the match is part of a network protocol pattern
                        if not re.search(protocol_pattern + re.escape(match), file_contents):
                            try:
                                # Attempt to decode binary match to string
                                decoded_path = match.decode('utf-8')

                                if any(decoded_path.startswith("/"+x) for x in BAD_MOUNTPOINTS):
                                    continue

                                if " " in decoded_path:
                                    continue

                                if decoded_path.endswith(".c"):
                                    # common reference to source code
                                    continue

                                referenced_paths.add(decoded_path)
                            except UnicodeDecodeError:
                                # Ignore decoding errors, as we might encounter binary paths or non-text content
                                pass

    return referenced_paths
    
def solve_unification(file_map, verbose=False):
    '''
    File map maps input files -> {path -> member info}
    Our goal here is to find an arrangement of file systems mounted into existing directories such that
    we get the maximum number of files
    '''
    # Our goal now is to find the biggest filesystem based on number of files that's a valid rootfs
    best_scenario = None
    best_score = 0

    # First do a static pass through file_map to identify referenced paths
    #referenced_paths = find_referenced_paths(list(file_map.keys()))
    #print(f"Referenced paths: {len(referenced_paths)}")
    #for p in referenced_paths:
    #    print(p)

    # Start from each filesystem and search for the best scenario where it's the root
    for root_file in file_map.keys():
        scenario, score = find_best_score({"./": root_file}, file_map)

        if not is_valid_rootfs(scenario, file_map):
            if verbose:
                print(f"Skipping scenario {scenario} with score {score} as it's not a valid rootfs")
            continue

        if score > best_score:
            best_scenario = scenario
            best_score = score

    if not best_scenario:
        return None

    # Realize the best scenario
    realized_fs = realize_fs(best_scenario, file_map)

    # Print or return the best scenario
    if verbose:
        print(f"Best unification scenario with {len(best_scenario)} filesystems has score {best_score}")
        for mount_point, file in best_scenario.items():
            print(f"\tMount {file} at {mount_point}")

        calculate_score(realized_fs, report=True)

    return best_scenario

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

def is_linux_rootfs(start_dir : Path):
    key_dirs = {'bin', 'etc', 'lib', 'usr', 'var'}
    critical_files = {'bin/sh', 'etc/passwd'}
    min_required = (len(key_dirs) + len(critical_files)) // 2  # Minimum number of key dirs and files

    # Check how many of the key directories are present
    present_dirs = set()
    for d in key_dirs:
        if (start_dir / d).is_dir():
            present_dirs.add(d)
    
    # Check how many of the critical files are present
    present_files = set()
    for f in critical_files:
        if (start_dir / f).exists():
            present_files.add(f)

    return (len(present_dirs) + len(present_files)) >= min_required

def find_extractions(start_dir, min_executables=10, extractor=None, verbose=False):
    filesystems = defaultdict(lambda: {'score': 0, 'size': 0, 'path': '', 'nfiles': 0, 'is_root': False, 'executables': 0})

    for root, dirs, files in os.walk(start_dir):
        root_path = Path(root)

        # Name must end with _extract for us to treat it as a root extraction (unblob)
        if (extractor == "unblob" and not root_path.name.endswith('_extract')):
            continue

        size, nfiles, executables = get_dir_size_exes(root_path)

        if nfiles == 0:
            # Skip empty extraction
            continue

        is_root = is_linux_rootfs(root_path)
        if is_root and executables < min_executables:
            # Expect rootfs to have at least 10 executables - skip (bad extract?)
            print(f"Skipping potential rootfs {root_path} with {nfiles} files as it only has {executables} executables")
            continue

        filesystems[str(root_path)].update({'size': size, 'nfiles': nfiles, 'path': str(root_path), 'is_root': is_root, 'executables': executables})

    ranked_filesystems = sorted(filesystems.values(), key=lambda x: (-x['is_root'], -x['executables'], -x['size'], -x['score']))

    if verbose:
        for fs in ranked_filesystems:
            friendly_path = fs['path'].replace(start_dir, '')
            print(f"{extractor if extractor else ''} found filesystem: {friendly_path} with {fs['nfiles']:,} files, {fs['size']:,} bytes, {fs['executables']} executables")

    return ranked_filesystems

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

def render_mounts(mounts, output_tar_base, scratch_dir="/tmp"):
    '''
    Given a dictionary of mount points like:
        ./: file1.tar.gz
        ./mnt/: file2.tar.gz
        ./mnt/othermnt: file3.tar.gz

    Produce a new tar archive that combines the input tar archives into a unified filesystem
    (archive) with this structure
    '''
    root_dir = tempfile.mkdtemp(dir=scratch_dir)
    try:
        # Extract each tar file to its specified mount point
        for mount_point, file_path in mounts.items():
            extract_to = os.path.join(root_dir, mount_point.strip("./"))
            os.makedirs(extract_to, exist_ok=True)  # Ensure the target directory exists
            with tarfile.open(file_path, "r:gz") as tar:
                tar.extractall(path=extract_to)

        # Now use helper to compress the whole thing
        _tar_fs(root_dir, output_tar_base)
        print(f"Unified tar archive created at: {output_tar_base}.tar.gz with {len(mounts)} filesystems")
    finally:
        shutil.rmtree(root_dir)

def extract_and_process(extractor, infile, outfile_base, scratch_dir, verbose):
    with tempfile.TemporaryDirectory(dir=scratch_dir) as extract_dir:
        log_file = f"{outfile_base}.{extractor}.log"
        start_time = time.time()
        _extract(extractor, infile, Path(extract_dir), log_file)
        post_extract = time.time()
        if verbose:
            print(f"{extractor} complete after {post_extract - start_time:.2f}s")

        # TODO: Grep within extract dir to find filesystem references in all extracted files
        # We'll use this later during unification as a set of inputs

        # Collect all filesystems and archive each
        all_filesystems = find_extractions(extract_dir, min_executables=0, extractor=extractor, verbose=verbose)
        if verbose:
            print(f"Found {len(all_filesystems)} filesystems")

        if len(all_filesystems) == 0:
            print(f"No filesystems found in {infile} - aborting")
            return

        # Create tar archives for each identified filesystem
        for idx, fs in enumerate(all_filesystems):
            tarbase = f"{outfile_base}.{extractor}.{idx}"
            _tar_fs(fs['path'], tarbase)
            #archive_hash = subprocess.run(["sha1sum", f"{tarbase}.tar.gz"], capture_output=True, text=True).stdout.split()[0]

        file_map = {}
        for idx, fs in enumerate(all_filesystems):
            file = f"{outfile_base}.{extractor}.{idx}.tar.gz"
            file_map[file] = {}
            with tarfile.open(file, "r:gz") as tar:
                for member in tar.getmembers():
                    file_map[file][member.name] = member

        mounts = solve_unification(file_map, verbose=verbose)
        if not mounts:
            print(f"Could not unify {len(all_filesystems)} filesystems to produce a valid rootfs")
            return

        render_mounts(mounts, f"{outfile_base}.rootfs", scratch_dir)
        # Report mounts into .txt output
        with open(f"{outfile_base}.mounts.txt", "w") as f:
            for mount_point, file in mounts.items():
                f.write(f"{mount_point}: {file}\n")

if __name__ == "__main__":
    os.umask(0o000)
    if os.geteuid() != 0:
        print("This script must be run as (fake)root")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Process some files.")
    parser.add_argument("infile", type=str, help="Input file")
    parser.add_argument("outfile", nargs='?', type=str, help="Output file base (optional). Default is infile without extension.")
    parser.add_argument("scratch_dir", nargs='?', default="/tmp/", type=str, help="Scratch directory (optional). Default /tmp")
    parser.add_argument("--verbose", action='store_true', help="Enable verbose output")
    parser.add_argument("--force", action='store_true', help="Overwrite existing output file")

    args = parser.parse_args()

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

    extract_and_process("unblob", args.infile, args.outfile, args.scratch_dir, args.verbose)