use std::cmp::Reverse;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

use super::directory_executables::{get_dir_executable_info, ExecutableInfo};

const DEFAULT_MIN_EXECUTABLES: usize = 10;
const MAX_EXPLORE_DEPTH: usize = 15;

pub const KEY_DIRS: &[&str] = &["bin", "etc", "lib", "usr", "var"];
pub const CRITICAL_FILES: &[&str] = &["bin/sh", "etc/passwd"];

const MIN_REQUIRED: usize = (KEY_DIRS.len() + CRITICAL_FILES.len()) / 2;

pub struct PrimaryFilesystem {
    path: PathBuf,
    size: u64,
    num_files: usize,
    key_file_count: usize,
    executables: usize,
}

pub fn find_linux_filesystems(
    start_dir: &Path,
    min_executables: Option<usize>,
    verbose: bool,
    extractor_name: &str,
) -> Vec<PrimaryFilesystem> {
    let mut filesystems = Vec::new();
    let min_executables = min_executables.unwrap_or(DEFAULT_MIN_EXECUTABLES);

    for entry in WalkDir::new(start_dir)
        .max_depth(MAX_EXPLORE_DEPTH)
        .into_iter()
        .filter_entry(|entry| entry.file_type().is_dir())
    {
        let Ok(entry) = entry else { continue };

        let mut total_matches = 0;
        let root = entry.path();

        for dir in KEY_DIRS {
            if root.join(dir).is_dir() {
                total_matches += 1;
            }
        }

        for file in CRITICAL_FILES {
            if root.join(file).exists() {
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
        }
    }

    filesystems.sort_by_key(|fs| Reverse((fs.executables, fs.size, fs.key_file_count)));

    filesystems
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn assert_key_dirs_sorted() {
        assert!(KEY_DIRS.is_sorted());
    }

    #[test]
    fn test_walkdir() {
        for entry in WalkDir::new(".") {
            let entry = entry.unwrap();

            println!("{}", entry.path().display());
        }
    }
}
