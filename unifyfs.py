import tarfile
import glob
from copy import deepcopy
from pathlib import Path, PurePath
import subprocess
from sys import argv
import os
import shutil
import tempfile

BAD_MOUNTPOINTS  = ["tmp", "dev", "sys"]

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
                        best_mount_points = new_mount_points
                        best_score = this_score
                        best_mount_points, best_score = find_best_score(best_mount_points, file_map)

    if not found_improvement:
        # We didn't find a better mount point, so return
        return best_mount_points, best_score

    # Recurse
    return find_best_score(best_mount_points, file_map)



def unify(file_map):
    '''
    File map maps input files -> {path -> member info}
    Our goal here is to find an arrangement of file systems mounted into existing directories such that
    we get the maximum number of files
    '''
    # Our goal now is to find the biggest filesystem based on number of files
    best_scenario = None
    best_score = 0

    #best_scenario, best_score = find_best_score({"./": "test/egs7228fp_fw_1.05.05_141210-1751.unblob.1.tar.gz",
    #                                            #"./sqfs/": "test/egs7228fp_fw_1.05.05_141210-1751.unblob.0.tar.gz",
    #                                             }, file_map)

    # Generate mounting scenarios
    for root_file in file_map.keys():
        scenario, score = find_best_score({"./": root_file}, file_map)
        if score > best_score:
            best_scenario = scenario
            best_score = score

    # Print or return the best scenario
    print(f"Best scenario has score {best_score}")
    for mount_point, file in best_scenario.items():
        print(f"\tMount {file} at {mount_point}")

    realized_fs = realize_fs(best_scenario, file_map)
    best_score, dangling_link_targets = calculate_score(realized_fs, report=True)

    return best_scenario

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

def render_mounts(mounts, output_tar_base=None):
    '''
    Given a dictionary of mount points like:
        ./: file1.tar.gz
        ./mnt/: file2.tar.gz
        ./mnt/othermnt: file3.tar.gz

    Produce a new tar archive that combines the input tar archives into a unified filesystem
    (archive) with this structure
    '''
    if not output_tar_base:
        output_tar_base = "unified"

    # Create a temporary directory for the unified filesystem
    root_dir = tempfile.mkdtemp()

    try:
        # Step 1: Extract each tar file to its specified mount point
        for mount_point, file_path in mounts.items():
            extract_to = os.path.join(root_dir, mount_point.strip("./"))
            os.makedirs(extract_to, exist_ok=True)  # Ensure the target directory exists
            with tarfile.open(file_path, "r:gz") as tar:
                tar.extractall(path=extract_to)

        # Now use helper
        _tar_fs(root_dir, output_tar_base)
        print(f"Unified tar archive created at: {output_tar_base}.tar.gz")
    finally:
        # Step 3: Clean up the temporary directory
        shutil.rmtree(root_dir)

def main(tarfile_base, unify_base=None):

    if tarfile_base.endswith(".tar.gz"):
        # Trim
        tarfile_base = ".".join(tarfile_base.split(".")[:-3]) # -3: same extractor, -4 both extractors

    file_map = {} # path -> tarfile path
    for file in glob.glob(f"{tarfile_base}*.tar.gz"):
        file_map[file] = {}
        # Collect list of files
        with tarfile.open(file, "r:gz") as tar:
            for member in tar.getmembers():
                file_map[file][member.name] = member

    mounts = unify(file_map)
    render_mounts(mounts, unify_base)

if __name__ == '__main__':
    if len(argv) < 1:
        raise ValueError(f"USAGE {argv[0]}: <tarfile_base> [unify_base]")
    
    main(argv[1], argv[2] if len(argv) > 2 else None)
    