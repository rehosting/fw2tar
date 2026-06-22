use std::cmp::Reverse;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

use super::directory_executables::{get_dir_executable_info, ExecutableInfo};

const DEFAULT_MIN_EXECUTABLES: usize = 10;
const MAX_EXPLORE_DEPTH: usize = 15;

pub const KEY_DIRS: &[&str] = &["bin", "etc", "lib", "usr", "var"];
pub const CRITICAL_FILES: &[&str] = &["bin/sh", "etc/passwd"];

const MIN_REQUIRED: usize = (KEY_DIRS.len() + CRITICAL_FILES.len()) / 2;

#[derive(Debug, Clone)]
pub struct PrimaryFilesystem {
    pub path: PathBuf,
    pub size: u64,
    pub num_files: usize,
    pub key_file_count: usize,
    pub executables: usize,
}

pub fn find_linux_filesystems(
    start_dir: &Path,
    min_executables: Option<usize>,
    extractor_name: &str,
) -> Vec<PrimaryFilesystem> {
    let mut filesystems = Vec::new();
    let min_executables = min_executables.unwrap_or(DEFAULT_MIN_EXECUTABLES);

    log::info!("Searching {start_dir:?}");

    for entry in WalkDir::new(start_dir)
        .max_depth(MAX_EXPLORE_DEPTH)
        .into_iter()
        .filter_entry(|entry| entry.file_type().is_dir())
    {
        let Ok(entry) = entry else { continue };

        //log::trace!("looking at {:?}", entry.path());

        let mut total_matches = 0;
        let root = entry.path();

        for dir in KEY_DIRS {
            if root.join(dir).exists() {
                //log::trace!("{dir} found in {root:?}");
                total_matches += 1;
            }
        }

        for file in CRITICAL_FILES {
            if root.join(file).exists() {
                //log::trace!("{file} found in {root:?}");
                total_matches += 1;
            }
        }

        if total_matches >= MIN_REQUIRED {
            let ExecutableInfo {
                total_executables,
                total_size,
                total_files,
            } = get_dir_executable_info(root);

            if total_executables >= min_executables {
                log::info!("{root:?}: {total_executables}, {total_size}, {total_files}");

                filesystems.push(PrimaryFilesystem {
                    path: root.to_owned(),
                    size: total_size,
                    num_files: total_files,
                    key_file_count: total_matches,
                    executables: total_executables,
                })
            } else {
                log::warn!("Extractor {extractor_name} did not find enough executables ({total_executables} < {min_executables})")
            }
        } else if total_matches > 0 {
            log::info!("Directory {} had {total_matches}", root.display());
        }
    }

    filesystems.sort_by_key(|fs| Reverse((fs.executables, fs.size, fs.key_file_count)));

    filesystems
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use tempfile::TempDir;

    #[test]
    fn assert_key_dirs_sorted() {
        // `find_linux_filesystems` relies on simple membership, but keeping the
        // list sorted makes review/diffs predictable.
        assert!(KEY_DIRS.is_sorted());
    }

    /// Build a minimal but convincing Linux rootfs under `root`: every key dir,
    /// both critical files, and `n_exec` executable files.
    fn make_rootfs(root: &Path, n_exec: usize) {
        for dir in KEY_DIRS {
            fs::create_dir_all(root.join(dir)).unwrap();
        }
        fs::write(root.join("bin/sh"), b"#!/bin/sh\n").unwrap();
        fs::write(root.join("etc/passwd"), b"root:x:0:0:root:/root:/bin/sh\n").unwrap();

        for i in 0..n_exec {
            let path = root.join("bin").join(format!("prog{i}"));
            fs::write(&path, b"\x7fELF").unwrap();
            let mut perms = fs::metadata(&path).unwrap().permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&path, perms).unwrap();
        }
    }

    #[test]
    fn detects_a_complete_rootfs() {
        let tmp = TempDir::new().unwrap();
        make_rootfs(tmp.path(), 5);

        let found = find_linux_filesystems(tmp.path(), Some(3), "test");

        assert_eq!(found.len(), 1, "expected exactly one rootfs");
        assert_eq!(found[0].path, tmp.path());
        assert!(found[0].executables >= 5);
        assert_eq!(found[0].key_file_count, KEY_DIRS.len() + CRITICAL_FILES.len());
    }

    #[test]
    fn rejects_rootfs_with_too_few_executables() {
        let tmp = TempDir::new().unwrap();
        make_rootfs(tmp.path(), 2);

        // Default threshold (10) should reject a rootfs with only 2 executables.
        let found = find_linux_filesystems(tmp.path(), None, "test");
        assert!(found.is_empty(), "should not accept a rootfs below the executable threshold");
    }

    #[test]
    fn rejects_directory_missing_key_paths() {
        let tmp = TempDir::new().unwrap();
        // Only one key dir present (1 < MIN_REQUIRED): not a rootfs.
        fs::create_dir_all(tmp.path().join("bin")).unwrap();
        fs::write(tmp.path().join("bin/sh"), b"x").unwrap();

        let found = find_linux_filesystems(tmp.path(), Some(0), "test");
        assert!(found.is_empty());
    }

    #[test]
    fn ranks_richer_rootfs_first() {
        let tmp = TempDir::new().unwrap();
        let small = tmp.path().join("small");
        let big = tmp.path().join("big");
        make_rootfs(&small, 4);
        make_rootfs(&big, 12);

        let found = find_linux_filesystems(tmp.path(), Some(3), "test");
        assert_eq!(found.len(), 2);
        // Sorted by (executables, size, key_file_count) descending.
        assert_eq!(found[0].path, big);
        assert_eq!(found[1].path, small);
    }
}
