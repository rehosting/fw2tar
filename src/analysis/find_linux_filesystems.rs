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
