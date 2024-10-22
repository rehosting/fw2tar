import string
import os
import tempfile
import subprocess
import re
from copy import deepcopy

from collections import defaultdict
from typing import Dict, Set, List, Tuple, Optional

from .common import FilesystemInfo, FilesystemRepository

INVALID_ROOTS = ("./proc", "./sys", "./dev", "./tmp")

class FilesystemUnifier:
    def __init__(self, repository: FilesystemRepository):
        self.repository = repository

    def unify(self) -> Dict[str, str]:
        """
        Main method to unify filesystems. Tries each filesystem as a potential root
        and returns the best overall configuration.

        Returns:
            Dict[str, str]: A mapping of mount points to filesystem names.
        """
        best_mount_points = {}
        best_score = float('-inf')

        # Try each filesystem as a potential root - with each consider how we could mount
        # others
        for root_fs_name, root_fs_info in self.repository.get_all_filesystems().items():
            if not self._could_be_root(root_fs_info):
                continue
            initial = {"./": root_fs_name}
            mount_points, score = self._try_unify_from(initial)
            if score > best_score:
                best_score = score
                best_mount_points = mount_points

        return best_mount_points

    def _could_be_root(self, fs_info: FilesystemInfo) -> bool:
        # Count how many "standard" files we see in the filesystem.
        # If we have at least 3 of these, we'll saw it could be a root
        standard_dirs = set([f"./{x}" for x in "var usr run bin sbin sys tmp etc home lib media mnt opt proc bin dev root srv".split()])
        standard_files = set(["./etc/passwd", "./etc/fstab", "./bin/ls", "./bin/bash", "./bin/busybox"])

        combined = standard_dirs | standard_files

        count = 0
        for checked in combined:
            if checked in fs_info.paths:
                count += 1

        return count >= 3


    def _try_unify_from(self, mount_points: Dict[str, str]) -> Tuple[Dict[str, str], float]:
        """
        Recursively tries to unify filesystems starting from the given mount points.

        Args:
            mount_points (Dict[str, str]): Current mapping of mount points to filesystem names.

        Returns:
            Tuple[Dict[str, str], float]: Best mount points configuration and its score.
        """
        unresolved_paths = self._get_unresolved_paths(mount_points)
        remaining_filesystems = set(self.repository.get_all_filesystems().keys()) - set(mount_points.values())

        best_score = self._calculate_configuration_score(mount_points, unresolved_paths)

        print(f"{mount_points} has score {best_score}. Trying to improve with more filesystems...")

        # Collect symlinks that exist within the mount points we've defined
        # We *should not* add a filesystem at a symlink, that wouldn't really make sense
        # though we could resolve them and analyze it more
        # For example if we have ./var -> ./tmp, we shouldn't place anything at ./var because
        # we don't want to add things to /tmp.
        # If we have ./etc -> ./etc/var we should place things at ./etc/var and not at ./etc
        symlinks = {}
        for mount_point, existing_name in mount_points.items():
            fs_info = self.repository.get_filesystem(existing_name)
            for link, target in fs_info.links.items():
                link_dest = os.path.join(os.path.dirname(mount_point + link[2:]), target)
                if not link_dest.startswith("."):
                    if link_dest.startswith("/"):
                        prefix = "."
                    else:
                        prefix = "./"
                    link_dest = prefix + link_dest

                symlinks[mount_point + link[2:]] = link_dest

        best_config = mount_points.copy()

        for fs_name in remaining_filesystems:
            fs_info = self.repository.get_filesystem(fs_name)
            mount_point, score_improvement = self._find_best_mount_point(mount_points, fs_info, unresolved_paths, symlinks)

            if mount_point and score_improvement > 0:
                new_mount_points = mount_points.copy()
                new_mount_points[mount_point] = fs_name
                new_config, new_score = self._try_unify_from(new_mount_points)

                if new_score > best_score:
                    best_score = new_score
                    best_config = new_config

        return best_config, best_score

    def _find_best_mount_point(self, cur_mounts: Dict[str, str], fs_info: FilesystemInfo, unresolved_paths: Set[str], symlinks: Dict[str, str]) -> Tuple[Optional[str], float]:
        """
        Finds the best mount point for a filesystem based on how many unresolved paths it can resolve.

        Args:
            cur_mounts (Dict[str, str]): Current mapping of mount points to filesystem names.
            fs_info (FilesystemInfo): Information about the filesystem to evaluate.
            unresolved_paths (Set[str]): Current set of unresolved paths.

        Returns:
            Tuple[Optional[str], float]: The best mount point and the score improvement, or (None, 0) if no suitable mount point is found.
        """
        best_mount_point = None
        best_score_improvement = 0
        visible_paths = self._get_visible_paths(cur_mounts)
        potential_mounts = self._find_potential_mount_points(cur_mounts, fs_info, unresolved_paths, symlinks)

        for potential_mount_point in potential_mounts:
            resolved_paths = self._get_resolved_paths(visible_paths, potential_mount_point, fs_info, unresolved_paths)
            total_files_in_mount = len(fs_info.paths)

            new_mounts = deepcopy(cur_mounts)
            new_mounts[potential_mount_point] = fs_info.name
            # Combine all visible paths into a single set
            total_files_with_mount = set.union(*self._get_visible_paths(new_mounts).values())

            # XXX: We don't want to lose/shadow too many files. Specifically we probably don't want to lose files
            # from our root filesystem, but shadowing files is generally probably bad
            lost_files = []
            for _, files in visible_paths.items():
                lost_files.extend([x for x in files if x.startswith(potential_mount_point)])

            print(f"\t{cur_mounts} + {fs_info.name} @ {potential_mount_point} resolves {len(resolved_paths)} paths, adds {total_files_in_mount} files, loses {len(lost_files)} to get {len(total_files_with_mount)} total files")
            print(f"\t\t {' '.join(resolved_paths[:10])}")

            # XXX: is our improvement just the number of resolved paths?
            # What if this mount just resolves like 1 path and adds a bunch of broken references? On the other hand, what if it's just 1 path and we're fixing it
            if len(lost_files) > 5:
                # Probably bad, we don't want to shadow too many files
                score_improvement = 0
            elif len(resolved_paths) > 2:
                # If we resolve more than 2 paths, we're probably doing well
                score_improvement = len(resolved_paths)
            elif len(resolved_paths) == 0:
                score_improvement = -1
            else:
                # If we only resolve a couple paths, things could be good. Or bad.
                if len("".join([x.replace(potential_mount_point,'') for x in resolved_paths])) > 10:
                    # The names are long -> more likely good
                    score_improvement = len(resolved_paths)

                if not any([x in string.ascii_letters for x in "".join([x.replace(potential_mount_point,'') for x in resolved_paths]).split()]):
                    # The names are mostly non-ascii -> probably bad
                    score_improvement = 0

                elif total_files_in_mount < 10:
                    # Only a few files are in this mount point, less alignment
                    # is to be expected.
                    score_improvement = len(resolved_paths)
                else:
                    # Otherwise this is probably junk.
                    score_improvement = 0

            if score_improvement > best_score_improvement:
                best_score_improvement = score_improvement
                best_mount_point = potential_mount_point

        return best_mount_point, best_score_improvement

    def _calculate_configuration_score(self, mount_points: Dict[str, str], unresolved_paths: Set[str]) -> float:
        """
        Calculates a score for the current filesystem configuration. For now we're just saying the number of paths.
        This is probably too simple - adding more filesystems isn't good unless they actually resolve something

        Args:
            mount_points (Dict[str, str]): Current mapping of mount points to filesystem names.
            unresolved_paths (Set[str]): Set of paths that remain unresolved.

        Returns:
            float: The configuration score.
        """
        resolved_paths = sum(len(self.repository.get_filesystem(fs_name).paths) for fs_name in mount_points.values())
        #return resolved_paths - len(unresolved_paths) - (len(mount_points) * 10)  # Penalize number of mount points
        return resolved_paths


    def _get_unresolved_paths(self, mount_points: Dict[str, str]) -> Set[str]:
        """
        Identifies unresolved paths in the context of currently mounted filesystems.

        Filters out paths that are invalid linux paths or in /dev or /tmp

        Args:
            mount_points (Dict[str, str]): Current mapping of mount points to filesystem names.

        Returns:
            Set[str]: Set of unresolved paths.
        """
        unresolved_paths = set()
        visible_paths = self._get_visible_paths(mount_points)

        for mount_point, fs_name in mount_points.items():
            fs_info = self.repository.get_filesystem(fs_name)
            for reference in fs_info.references:
                if not self._path_is_resolved(reference, visible_paths):
                    unresolved_paths.add(reference)

        return unresolved_paths

    def _path_is_resolved(self, path: str, visible_paths: Dict[str, Set[str]]) -> bool:
        """
        Checks if a given path is resolved by any of the currently visible paths.

        Args:
            path (str): The path to check.
            visible_paths (Dict[str, Set[str]]): Mapping of mount points to their visible paths.

        Returns:
            bool: True if the path is resolved, False otherwise.
        """
        return any(path in paths for paths in visible_paths.values())

    @staticmethod
    def _get_relative_path(path: str, mount_point: str) -> str:
        """
        Calculates a relative path based on a mount point.

        Args:
            path (str): The full path.
            mount_point (str): The mount point.

        Returns:
            str: The relative path.
        """
        if path.startswith(mount_point):
            return path[len(mount_point):].lstrip('/')
        return path

    def _find_potential_mount_points(self, cur_mounts: Dict[str, str], fs_info: FilesystemInfo, unresolved_paths: Set[str], symlinks: Dict[str, str]) -> List[str]:
        """
        Finds potential mount points for a filesystem based on unresolved paths.

        Args:
            cur_mounts (Dict[str, str]): Current mapping of mount points to filesystem names.
            fs_info (FilesystemInfo): Information about the filesystem we're considering mounting.
            unresolved_paths (Set[str]): Current set of unresolved paths in the established filesystem.

        Returns:
            List[str]: List of potential mount points, sorted by the number of paths they would resolve.
        """
        # Step 1: Identify all potential mount points
        mount_point_candidates = defaultdict(set)
        for unresolved_path in unresolved_paths:
            unresolved_path = "." + unresolved_path  # Ensure path starts with .
            for fs_path in fs_info.paths:
                potential_mount_point = self._get_potential_mount_point(unresolved_path, fs_path)
                if potential_mount_point and potential_mount_point != '.':
                    while potential_mount_point in symlinks:
                        # "resolve" symlink
                        potential_mount_point = symlinks[potential_mount_point]

                    if self._is_unlikely_mount(potential_mount_point):
                        continue
                    if self._is_valid_new_mount_point(potential_mount_point, cur_mounts):
                        mount_point_candidates[potential_mount_point].add(unresolved_path)

        # Step 2: Evaluate each potential mount point
        potential_mount_points: Dict[str, int] = {}
        for mount_point, candidate_paths in mount_point_candidates.items():
            resolved_paths = self._get_resolved_paths(cur_mounts, mount_point, fs_info, unresolved_paths)
            potential_mount_points[mount_point] = len(resolved_paths)
            #print(f"Mounting {fs_info.name} at {mount_point} resolves {resolved_paths} paths")
            #print(f"\t", resolved_paths)

        # Step 3: Sort and return the results
        return sorted(potential_mount_points, key=potential_mount_points.get, reverse=True)

    def _is_unlikely_mount(self, potential_mount_point: str) -> bool:
        '''
        We see some common patterns of invalid mount points - save some time by skipping them.
        Shouldn't really make a difference in terms of end results, but simplifies debugging
        '''
        # Domain names
        if "www." in potential_mount_point or \
                potential_mount_point.endswith(".com") or \
                ".com/" in potential_mount_point:
            return True

        # compiler directories
        if "-none-" in potential_mount_point or \
                "-gcc-" in potential_mount_point or \
                "-clang-" in potential_mount_point or \
                "-gnu" in potential_mount_point:
            return True

        # Long
        if len(potential_mount_point) > 30:
            return True

        # Otherwise it's probably fine
        return False


    def _is_valid_new_mount_point(self, new_mount: str, cur_mounts: Dict[str, str]) -> bool:
        """
        Checks if a new mount point is valid given the current mount points.

        Args:
            new_mount (str): The potential new mount point to evaluate.
            cur_mounts (Dict[str, str]): Current mapping of mount points to filesystem names.

        Returns:
            bool: True if the new mount point is valid, False otherwise.
        """
        if new_mount in INVALID_ROOTS:
            return False

        for existing_mount in cur_mounts:
            if new_mount == existing_mount:
                return False  # Prevent mounting at the same point
            if existing_mount.startswith(new_mount + '/'):
                return False  # Prevent mounting a parent directory of an existing mount
        return True

    @staticmethod
    def _get_potential_mount_point(unresolved_path: str, fs_path: str) -> Optional[str]:
        """
        Determines a potential mount point by comparing an unresolved path with a filesystem path.

        For example if we have an unresolved path of ./mnt/foo/zoo and fs_path is ./foo/zoo
        we should return ./mnt as mounting the fs_path at ./mnt would resolve the unresolved path.

        Args:
            unresolved_path (str): An unresolved path.
            fs_path (str): A path in the filesystem being considered.

        Returns:
            Optional[str]: A potential mount point, or None if no suitable mount point is found.
        """

        # Expect both to be ./something/... paths
        if not unresolved_path.startswith("./") or not fs_path.startswith("./"):
            raise ValueError(f"Paths must start with ./ but got {unresolved_path} and {fs_path}")

        # Check if unresolved_path ends with fs_path after dropping leading .s

        if unresolved_path.endswith(fs_path[1:]):
            result = unresolved_path[:-len(fs_path) + 1]
            if not result.startswith(INVALID_ROOTS):
                return result

        return None

    def _get_resolved_paths(self, visible_paths: Dict[str, Set[str]], mount_point: str, fs_info: FilesystemInfo, unresolved_paths: Set[str]) -> List[str]:
        """
        Get the unresolved paths that would be resolved by mounting a filesystem at a given point.

        Args:
            visible_paths (Dict[str, Set[str]]): Mapping of mount points to their visible paths.
            mount_point (str): The potential mount point.
            fs_info (FilesystemInfo): Information about the filesystem.
            unresolved_paths (Set[str]): Current set of unresolved paths.

        Returns:
            List[str]: List of paths that would be resolved.
        """
        return [x for x in unresolved_paths if self._path_would_be_resolved(visible_paths, "." + x, mount_point, fs_info)]

    def _path_would_be_resolved(self, visible_paths: Dict[str, Set[str]], unresolved_path: str, mount_point: str, fs_info: FilesystemInfo) -> bool:
        """
        Checks if an unresolved path would be resolved by mounting a filesystem at a given point.

        Args:
            visible_paths (Dict[str, Set[str]]): Mapping of mount points to their visible paths.
            unresolved_path (str): The unresolved path to check.
            mount_point (str): The potential mount point.
            fs_info (FilesystemInfo): Information about the filesystem.

        Returns:
            bool: True if the path would be resolved, False otherwise.
        """
        # Check if the path is already resolved by existing visible paths
        if any(unresolved_path in paths for paths in visible_paths.values()):
            return False

        if unresolved_path.startswith(mount_point):
            relative_path = unresolved_path[len(mount_point):].lstrip('/')
            return any(fs_path.endswith(relative_path) for fs_path in fs_info.paths)
        return False

    def _get_visible_paths(self, mount_points: Dict[str, str]) -> Dict[str, Set[str]]:
        """
        Calculate the visible paths for each mount point based on the current filesystem structure.

        Args:
            mount_points (Dict[str, str]): Current mapping of mount points to filesystem names.

        Returns:
            Dict[str, Set[str]]: A mapping of mount points to their visible paths.
        """
        visible_paths = {}
        sorted_mount_points = sorted(mount_points.items(), key=lambda x: len(x[0]), reverse=True)

        for mount_point, fs_name in sorted_mount_points:
            fs_info = self.repository.get_filesystem(fs_name)
            visible_paths[mount_point] = set()
            for path in fs_info.paths:
                full_path = os.path.join(mount_point, path.lstrip('./'))
                if not any(full_path.startswith(other_mount) for other_mount in visible_paths if other_mount != mount_point):
                    visible_paths[mount_point].add(full_path)

        return visible_paths

    def create_archive(self, archive_dir, mounts, output):
        # Create a temporary directory, then extract filesystems from self.repository at the mount
        # points and package it up
        with tempfile.TemporaryDirectory() as temp_dir:
            for mount_point, fs_name in mounts.items():
                fs_info = self.repository.get_filesystem(fs_name)
                src = os.path.join(archive_dir, fs_info.name)
                dest = os.path.join(temp_dir, mount_point)

                # Ensure dest is within temp_dir
                if not os.path.commonpath([temp_dir, dest]) == temp_dir:
                    raise ValueError(f"Destination {dest} is not within {temp_dir}")

                # Create the directory if it doesn't exist
                os.makedirs(dest, exist_ok=True)

                # Extract
                subprocess.check_output(["tar", "xf", src, "-C", dest])


            # All done - package it up
            subprocess.check_output(["tar", "czf", output, "-C", temp_dir, "."])
