import glob
import os
import re
import io
import shutil
import subprocess
import tarfile
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Dict, Set, Tuple, List, Optional

from typing import NamedTuple

class UnificationScore(NamedTuple):
    resolved_paths: int
    mounted_filesystems: int
    unresolved_paths: int

    def __gt__(self, other):
        if self.resolved_paths != other.resolved_paths:
            return self.resolved_paths > other.resolved_paths
        if self.mounted_filesystems != other.mounted_filesystems:
            return self.mounted_filesystems > other.mounted_filesystems
        return self.unresolved_paths < other.unresolved_paths

class FilesystemUnifier:
    BAD_MOUNTPOINTS = ["tmp", "dev", "sys", "proc"]

    def __init__(self):
        self.file_map: Dict[str, Dict[str, tarfile.TarInfo]] = {}
        self.mount_points: Dict[str, str] = {}
        self.referenced_paths: Dict[str, Set[str]] = {}
        self.archive_base: Optional[str] = None

    def load_filesystems(self, input_path: str):
        # If tarfile_base is a directory, we glob within it.
        # If it's a .tar.gz we take the stem
        if input_path.endswith(".tar.gz"):
            glob_target = f"{input_path[:-7]}*.tar.gz"
            input_path = os.path.dirname(input_path)
        elif os.path.isdir(input_path):
            glob_target = f"{input_path}/*.tar.gz"
        else:
            raise ValueError(f"Input path must be a directory or a .tar.gz file {input_path} is neither")
        self.archive_base = os.path.dirname(glob_target)

        for file in glob.glob(glob_target):
            with tarfile.open(file, "r:gz") as tar:
                rel_file = os.path.relpath(file, input_path)
                self.file_map[rel_file] = {member.name: member for member in tar.getmembers() if member.name != '.'}

    def unify(self, referenced_paths: Optional[Dict[str, Set[str]]] = None) -> Dict[str, str]:
        self.referenced_paths = referenced_paths or self._generate_referenced_paths()

        potential_roots = self._identify_root_filesystems()
        if not potential_roots:
            raise ValueError("No valid root filesystem found")

        best_score = None
        best_root = None
        best_mount_points = None

        for root_fs, _ in potential_roots:
            score = self._evaluate_root_filesystem(root_fs)
            if best_score is None or score > best_score:
                best_score = score
                best_root = root_fs
                best_mount_points = self._unify_with_root(root_fs)

        self.mount_points = best_mount_points
        return self.mount_points

    def _unify_with_root(self, root_fs: str) -> Dict[str, str]:
        mount_points = {"./": root_fs}
        unresolved_paths = set(self.referenced_paths[root_fs])

        for fs_name, fs_content in self.file_map.items():
            if fs_name == root_fs:
                continue
            potential_mount_points = self._find_best_mount_points(unresolved_paths, fs_content, self.referenced_paths.get(fs_name, set()))
            for mount_point, _ in potential_mount_points:
                if self._is_valid_mount_point(mount_point, mount_points):
                    mount_points[mount_point] = fs_name
                    unresolved_paths -= set(path for path in unresolved_paths if path.startswith(mount_point))
                    break

        return mount_points

    def _is_valid_mount_point(self, mount_point: str, existing_mount_points: Dict[str, str]) -> bool:
        # Check if the mount point is already used
        if mount_point in existing_mount_points:
            return False

        # Check if the mount point is a subdirectory of an existing mount point
        # No, this is fine.
        #for existing_point in existing_mount_points:
        #    if mount_point.startswith(existing_point) and mount_point != existing_point:
        #        print(f"Bad mount point, subdir {existing_point}, {mount_point}")
        #        return False

        return True

    def render_unified_filesystem(self, output_tar_base: str = "unified"):
        with tempfile.TemporaryDirectory() as root_dir:
            for mount_point, file_path in self.mount_points.items():
                extract_to = os.path.join(root_dir, mount_point.strip("./"))
                os.makedirs(extract_to, exist_ok=True)
                with tarfile.open(os.path.join(self.archive_base, file_path), "r:gz") as tar:
                    tar.extractall(path=extract_to)

            self._tar_fs(root_dir, output_tar_base)
            print(f"Unified tar archive created at: {output_tar_base}.tar.gz")

    def _evaluate_root_filesystem(self, root_fs: str) -> UnificationScore:
        temp_mount_points = {"./": root_fs}
        unresolved_paths = set(self.referenced_paths[root_fs])
        mounted_filesystems = 1

        for fs_name, fs_content in self.file_map.items():
            if fs_name == root_fs:
                continue
            potential_mount_points = self._find_best_mount_points(unresolved_paths, fs_content, self.referenced_paths.get(fs_name, set()))

            mounted = False
            for mount_point, score in potential_mount_points:
                if self._is_valid_mount_point(mount_point, temp_mount_points):
                    temp_mount_points[mount_point] = fs_name
                    mounted_filesystems += 1
                    unresolved_paths -= set(path for path in unresolved_paths if path.startswith(mount_point))
                    mounted = True
                    break  # Stop after finding the first valid mount point

        return UnificationScore(
            resolved_paths=len(self.referenced_paths[root_fs]) - len(unresolved_paths),
            mounted_filesystems=mounted_filesystems,
            unresolved_paths=len(unresolved_paths)
        )

    def _logical_resolve(self, path: Path, source: Path = None) -> Path:
        parts = list(path.parts)
        if source:
            parts = list(source.parts) + parts

        resolved_parts = []
        for part in parts:
            if part == '..':
                if resolved_parts:
                    resolved_parts.pop()
            elif part not in ('', '.', '/'):
                resolved_parts.append(part)

        resolved_path = Path(*resolved_parts)
        return Path("/") / resolved_path if not path.is_absolute() else resolved_path

    def _realize_fs(self) -> Dict[str, tarfile.TarInfo]:
        mount_fs = {}
        for mount_point, infile in self.mount_points.items():
            for path, detail in self.file_map[infile].items():
                mount_fs[os.path.join(mount_point, path.lstrip('./'))] = detail
        return mount_fs

    def calculate_score(self) -> Tuple[int, Set[str]]:
        mount_fs = self._realize_fs()
        total_files = sum(1 for member in mount_fs.values() if not member.issym())
        missing_files = set()

        for name, member in ((n, m) for n, m in mount_fs.items() if m.issym()):
            target = member.linkname
            target = str(self._logical_resolve(Path(target), Path(name).parent))
            target = f".{target}" if target.startswith('/') else f"./{target}"

            if any(target.startswith(f"./{x}") for x in self.BAD_MOUNTPOINTS):
                continue
            if target not in mount_fs:
                missing_files.add(target)
            else:
                total_files += 1

        target_files = set.union(*(self.referenced_paths[fs] for fs in self.mount_points.values()))
        missing_files.update(path for path in target_files if path not in mount_fs)

        return total_files, missing_files

    def _find_referenced_paths(self, tarname: str) -> Set[str]:
        path_regex = re.compile(rb'/[^/\0\n<>"\'! :\?]+(?:/[^/\0\n<>()%"\'! ;:\?]+)+')
        protocol_pattern = rb'(?:http|ftp|https)://'
        referenced_paths = set()

        with tarfile.open(tarname, "r:*") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    file_contents = tar.extractfile(member).read()
                    for match in re.findall(path_regex, file_contents):
                        if not re.search(protocol_pattern + re.escape(match), file_contents):
                            try:
                                decoded_path = match.decode('utf-8')
                                if any(decoded_path.startswith(f"/{x}") for x in self.BAD_MOUNTPOINTS):
                                    continue
                                if " " in decoded_path or decoded_path.endswith(".c"):
                                    continue
                                decoded_path = f".{decoded_path}" if decoded_path.startswith('/') else f"./{decoded_path}"
                                referenced_paths.add(decoded_path)
                            except UnicodeDecodeError:
                                pass

        return referenced_paths

    def _check_mountpoint(self, path: str, realized_fs: Dict[str, tarfile.TarInfo]) -> bool:
        if path in self.mount_points:
            return False
        if path not in realized_fs:
            path = path.rstrip('/')
        if path not in realized_fs:
            return True
        return len([x for x in realized_fs if x.startswith(f"{path}/")]) <= 1

    def _find_best_mount_points(self, unresolved_paths: Set[str], fs_content: Dict[str, 'tarfile.TarInfo'],
                                fs_referenced_paths: Set[str]) -> List[Tuple[str, int]]:
        #print(f"Trying to find ways to resolve: {unresolved_paths}")
        #print(f"By mounting filesystem with paths: {fs_content.keys()}")

        potential_mount_points = {}

        def find_common_suffix(path1: str, path2: str) -> str:
            parts1 = path1.split('/')
            parts2 = path2.split('/')
            common_suffix = []
            for p1, p2 in zip(reversed(parts1), reversed(parts2)):
                if p1 == p2:
                    common_suffix.append(p1)
                else:
                    break
            return '/'.join(reversed(common_suffix))

        for unresolved_path in unresolved_paths:
            normalized_unresolved = unresolved_path if unresolved_path.startswith('./') else f"./{unresolved_path.lstrip('/')}"
            if normalized_unresolved in ['.', './.']:
                continue

            for fs_path in fs_content.keys():
                if fs_path == '.':
                    continue
                normalized_fs_path = fs_path if fs_path.startswith('./') else f"./{fs_path.lstrip('/')}"

                common_suffix = find_common_suffix(normalized_unresolved, normalized_fs_path)

                if common_suffix:
                    # Determine the distinct prefix (potential mount point)
                    potential_mount_point = normalized_unresolved[:-len(common_suffix)].rstrip('/')
                    if not potential_mount_point or potential_mount_point == '.':
                        continue

                    # Check how many paths this mount point would resolve
                    resolved_paths = sum(1 for path in unresolved_paths
                                         if path.endswith(common_suffix) and
                                         path.startswith(potential_mount_point))

                    # Update the potential mount points
                    if potential_mount_point in potential_mount_points:
                        potential_mount_points[potential_mount_point] = max(potential_mount_points[potential_mount_point], resolved_paths)
                    else:
                        potential_mount_points[potential_mount_point] = resolved_paths

        # Sort the potential mount points by the number of resolved paths
        sorted_mount_points = sorted(potential_mount_points.items(), key=lambda x: x[1], reverse=True)
        # XXX: Why doesn't this work? We get the right value here

        #print("Potential mount points found:")
        #for mount_point, resolved_paths in sorted_mount_points:
        #    print(f"  {mount_point}: would resolve {resolved_paths} paths")

        return sorted_mount_points

    def _identify_root_filesystems(self) -> List[str]:
        # Loop through file_map and find the most promising rootfs
        standard_dirs = set(["./etc/", "./bin/", "./lib/", "./usr/", "./var/"])
        standard_files = set(["./etc/passwd", "./etc/fstab", "./bin/ls", "./bin/bash", "./bin/busybox"])

        results = {fs_name: 0 for fs_name in self.file_map}

        for fs_name, fs_content in self.file_map.items():
            dir_count = sum(1 for d in standard_dirs if any(file.startswith(d) for file in fs_content))
            file_count = sum(1 for f in standard_files if f in fs_content)
            results[fs_name] = dir_count + file_count

        # Need something to look right before we consider it a rootfs
        threshold = (len(standard_dirs) + len(standard_files)) // 4
        potential_roots = [(fs, score) for fs, score in results.items() if score > threshold]

        return sorted(potential_roots, key=lambda x: x[1], reverse=True)

    def _generate_referenced_paths(self) -> Dict[str, Set[str]]:
        '''
        Find all references in each filesystem using a static analysis.
        We want to find paths in binaries and scripts. We'll have (many) false positives.
        '''
        result = {}
        path_ref = re.compile(rb'/(?:[^/\0\n<>"\'! :\?]+/)*[^/\0\n<>"\'! :\?]+')
        protocol_pattern = re.compile(rb'(?:https?|ftp)://')

        for fs_name, fs_content in self.file_map.items():
            #print(f"Finding referenced paths in {fs_name}")
            this_referenced_paths = set()

            # Open the tarfile
            with tarfile.open(os.path.join(self.archive_base, fs_name), "r:gz") as tar:
                for member_name, member in fs_content.items():
                    if not member.isfile():
                        continue

                    try:
                        # Correctly extract and read the file content
                        file_content = tar.extractfile(member)
                        if file_content is None:
                            continue  # Skip if unable to extract file content
                        file_contents = file_content.read()
                    except (IOError, OSError) as e:
                        print(f"Error reading {member_name} from {fs_name}: {e}")
                        continue  # Skip files that can't be read

                    for match in re.finditer(path_ref, file_contents):
                        if protocol_pattern.search(file_contents, match.start() - 10, match.start()):
                            continue  # Skip URLs

                        try:
                            decoded_path = match.group().decode('utf-8')
                        except UnicodeDecodeError:
                            continue

                        if any(decoded_path.startswith(f"/{x}") for x in self.BAD_MOUNTPOINTS):
                            continue
                        if " " in decoded_path or decoded_path.endswith(".c"):
                            continue

                        # Normalize path. If it starts with /, we add a . to make it relative.
                        # If it starts with ./ we make it relative to the path.

                        if decoded_path.startswith('./'):
                            # Relative to directory name of the file with the reference.
                            normalized_path = os.path.join(os.path.dirname(member_name), decoded_path[2:])
                        else:
                            # Otherwise we just use the path as is.
                            normalized_path = decoded_path

                        if normalized_path.startswith('/'):
                            # Ensure paths are always relative
                            normalized_path = f".{decoded_path}"

                        this_referenced_paths.add(normalized_path)

            #print(f"Found {len(this_referenced_paths)} references in {fs_name}:", this_referenced_paths)
            result[fs_name] = this_referenced_paths

        return result

    def _tar_fs(self, rootfs_dir: str, tarbase: str):
        uncompressed_outfile = f"{tarbase}.tar"
        tar_command = [
            "tar", "-cf", uncompressed_outfile, "--sort=name", "--mtime=UTC 2019-01-01",
            "--exclude=0.tar", "--exclude=squashfs-root", "--exclude=*_extract",
            "--exclude=*.uncompressed", "--exclude=*.unknown", "--exclude=./dev",
            "-C", str(rootfs_dir), "."
        ]

        os.chmod(rootfs_dir, 0o755)
        subprocess.run(tar_command, check=True, capture_output=True, text=True)
        subprocess.run(["gzip", "--no-name", "-f", uncompressed_outfile], check=True, capture_output=True, text=True)
        os.chmod(f"{uncompressed_outfile}.gz", 0o644)

def main(tarfile_base: str, unify_base: str = None):
    unifier = FilesystemUnifier()
    # Load filesystems from base directory
    unifier.load_filesystems(tarfile_base)
    # Calculate optimal mount points
    mount_points = unifier.unify()
    print(mount_points)
    # Render into new filesystem
    unifier.render_unified_filesystem(unify_base if unify_base else tarfile_base + "unified")

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print(f"USAGE: {sys.argv[0]} <tarfile_base> [unify_base]")
        sys.exit(1)
    else:
        main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)