use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use walkdir::{DirEntry, WalkDir};

const EXECUTABLE_MASK: u32 = libc::S_IXUSR | libc::S_IXGRP | libc::S_IXOTH;

static BAD_SUFFIXES: &[&str] = &[
    "_extract",
    ".uncompressed",
    ".unknown",
    "cpio-root",
    "squashfs-root",
    "0.tar",
];

#[derive(Debug, Clone)]
pub struct ExecutableInfo {
    pub total_size: u64,
    pub total_files: usize,
    pub total_executables: usize,
}

pub fn get_dir_executable_info(dir: &Path) -> ExecutableInfo {
    let mut total_size = 0;
    let mut total_files = 0;
    let mut total_executables = 0;

    let ignore_extraction_artifacts = |entry: &DirEntry| {
        if entry.path() == dir {
            return true;
        }

        entry
            .path()
            .file_name()
            .map(|name| {
                name.to_str().map(|name| {
                    for suffix in BAD_SUFFIXES {
                        if name.ends_with(suffix) {
                            return false;
                        }
                    }

                    if name.starts_with("squashfs-root-") {
                        return false;
                    }

                    true
                })
            })
            .flatten()
            .unwrap_or(true)
    };

    for entry in WalkDir::new(dir)
        .into_iter()
        .filter_entry(ignore_extraction_artifacts)
    {
        let Ok(entry) = entry else { continue };
        let Ok(metadata) = entry.metadata() else {
            continue;
        };

        if metadata.is_file() {
            total_files += 1;

            if metadata.permissions().mode() & EXECUTABLE_MASK != 0 {
                total_executables += 1;
            }

            total_size += metadata.len();
        }
    }

    log::info!("{dir:?}: {total_size}, {total_files}, {total_executables}");

    ExecutableInfo {
        total_size,
        total_files,
        total_executables,
    }
}
