use std::cmp::Reverse;
use std::collections::BTreeMap;
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
    pub paths: BTreeMap<PathBuf,PathBuf>,
    pub size: u64,
    pub num_files: usize,
    pub key_file_count: usize,
    pub executables: usize,
}

pub fn find_linux_filesystems(
    start_dir: &Path,
    min_executables: Option<usize>,
    extractor_name: &str,
    internal_paths: Option<&BTreeMap<PathBuf,String>>,
) -> Vec<PrimaryFilesystem> {
    let mut filesystems = Vec::new();
    let min_executables = min_executables.unwrap_or(DEFAULT_MIN_EXECUTABLES);

    log::info!("Searching {start_dir:?}");
    let mut paths: BTreeMap<PathBuf,PathBuf> = BTreeMap::new(); 
    for entry in WalkDir::new(start_dir)
        .max_depth(MAX_EXPLORE_DEPTH)
        .into_iter()
        .filter_entry(|entry| entry.file_type().is_dir())
    {
        let Ok(entry) = entry else { continue };

        let mut total_matches = 0;
        let root = entry.path();

        for dir in KEY_DIRS {
            if root.join(dir).exists() {
                //log::debug!("{dir} found in {root:?}");
                total_matches += 1;
            }
        }
        if let Some(internals) = internal_paths{
            if let Some(result ) = internals.iter().find(|(_,v)| {entry.path().to_str().unwrap_or_default().contains(*v)}){
                if ! paths.contains_key(result.0) {
                    paths.insert(result.0.clone(), entry.path().to_path_buf());
                } else {
                    continue; //don't double add 
                }
            }
        }
        for file in CRITICAL_FILES {
            if root.join(file).exists() {
                //log::debug!("{file} found in {root:?}");
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
                let mut paths: BTreeMap<PathBuf,PathBuf> = BTreeMap::new(); 
                paths.insert(PathBuf::from("/"),root.to_owned());
                filesystems.push(PrimaryFilesystem {
                    paths: paths,
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
    filesystems = filesystems.iter().map(|file_system| -> PrimaryFilesystem{
        let root = file_system.paths.get(&PathBuf::from("/")).unwrap();
        let mut new_filesystem = file_system.clone();
        let mut new_paths = paths.clone();
        new_paths.insert(PathBuf::from("/"), root.to_owned());
        new_filesystem.paths =  new_paths;
        new_filesystem
    }).collect();
    filesystems.sort_by_key(|fs| Reverse((fs.executables, fs.size, fs.key_file_count)));
    //log::debug!("filesystems: {:?}",   filesystems);
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
