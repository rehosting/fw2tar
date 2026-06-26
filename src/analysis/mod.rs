use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Instant;
use std::{env, io};

use sha1::{Digest, Sha1};
use tempfile::TempDir;
use thiserror::Error;

pub mod directory_executables;
pub mod find_linux_filesystems;

use crate::archive::tar_fs;
use crate::extractors::{ExtractError, Extractor};
use crate::metadata::{Manifest, Metadata};
use find_linux_filesystems::find_linux_filesystems;

#[derive(Debug, Clone)]
pub struct ExtractionResult {
    pub extractor: &'static str,
    pub index: usize,
    pub size: u64,
    pub num_files: usize,
    pub primary: bool,
    pub archive_hash: String,
    pub file_node_count: usize,
    pub path: PathBuf,
    /// The manifest embedded in this archive's trailer (input metadata,
    /// extractor, stripped device nodes). Re-emitted as the sidecar for the
    /// winning result.
    pub manifest: Manifest,
}

#[derive(Error, Debug)]
pub enum ExtractProcessError {
    #[error("Failed to create temporary directory ({0:?})")]
    TempDirFail(io::Error),

    #[error("Failed to extract from file with extractor ({0})")]
    ExtractFail(ExtractError),

    #[error("Failed to find any filesystems in the extracted contents")]
    FailToFind,

    #[error("Failed to write tar archive ({0})")]
    TarFail(io::Error),

    #[error("Failed to hash tar archive ({0})")]
    HashFail(io::Error),
}

pub fn extract_and_process(
    extractor: &dyn Extractor,
    in_file: &Path,
    out_file_base: &Path,
    scratch_dir: Option<&Path>,
    verbose: bool,
    primary_limit: usize,
    results: &Mutex<Vec<ExtractionResult>>,
    metadata: &Metadata,
) -> Result<(), ExtractProcessError> {
    let extractor_name = extractor.name();

    let scratch_dir = scratch_dir
        .map(Path::to_path_buf)
        .unwrap_or_else(env::temp_dir);

    let temp_dir_prefix = format!("fw2tar_{extractor_name}");
    let temp_dir = TempDir::with_prefix_in(temp_dir_prefix, &scratch_dir)
        .map_err(ExtractProcessError::TempDirFail)?;

    let extract_dir = temp_dir.path();

    let log_file = extractor_log_path(out_file_base, extractor_name);

    let start_time = Instant::now();

    extractor
        .extract(in_file, extract_dir, &log_file, verbose)
        .map_err(ExtractProcessError::ExtractFail)?;

    let elapsed = start_time.elapsed().as_secs_f32();
    log::info!("{extractor_name} took {elapsed:.2} seconds");

    let rootfs_choices = find_linux_filesystems(extract_dir, None, extractor_name);

    if rootfs_choices.is_empty() {
        eprintln!("fw2tar:   {extractor_name} finished in {elapsed:.1}s — no Linux filesystem found");
        log::error!("No Linux filesystems found extracting {in_file:?} with {extractor_name}");
        return Err(ExtractProcessError::FailToFind);
    }

    eprintln!(
        "fw2tar:   {extractor_name} finished in {elapsed:.1}s — found {n} candidate filesystem(s)",
        n = rootfs_choices.len()
    );

    for (i, fs) in rootfs_choices.iter().enumerate() {
        if i >= primary_limit {
            eprintln!(
                "fw2tar:   {extractor_name}: skipping {n} more filesystem(s); raise --primary-limit if files are missing",
                n = rootfs_choices.len() - primary_limit
            );
            break;
        }

        let tar_path = {
            // Simple string append to avoid with_extension() being greedy
            let file_name = out_file_base.file_name().unwrap().to_string_lossy();
            out_file_base.with_file_name(format!("{}.{extractor_name}.{i}.tar.gz", file_name))
        };

        let (file_node_count, manifest) = tar_fs(&fs.path, &tar_path, metadata, extractor_name)
            .map_err(ExtractProcessError::TarFail)?;
        let archive_hash = sha1_file(&tar_path).map_err(ExtractProcessError::HashFail)?;

        results.lock().unwrap().push(ExtractionResult {
            extractor: extractor_name,
            index: i,
            size: fs.size,
            num_files: fs.num_files,
            primary: true,
            archive_hash,
            file_node_count,
            path: tar_path,
            manifest,
        });
    }

    drop(temp_dir);

    Ok(())
}

/// Per-extractor log file path next to the output base: `<base>.<extractor>.log`.
/// Uses `with_file_name` (string append) rather than `with_extension`, which
/// would greedily strip a dotted segment from the base. Shared by the producer
/// (extraction) and the post-run stray-artifact cleanup so the two never drift.
pub fn extractor_log_path(out_file_base: &Path, extractor_name: &str) -> PathBuf {
    let file_name = out_file_base.file_name().unwrap().to_string_lossy();
    out_file_base.with_file_name(format!("{file_name}.{extractor_name}.log"))
}

pub fn sha1_file(file: &Path) -> io::Result<String> {
    let bytes = std::fs::read(file)?;

    let mut hasher = Sha1::new();
    hasher.update(&bytes[..]);
    let result = hasher.finalize();

    Ok(format!("{result:x}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extractor_log_path_appends_without_greedy_strip() {
        // A dotted version segment in the base must survive (same contract as
        // the output archive path).
        assert_eq!(
            extractor_log_path(Path::new("/out/RAX54Sv2-V1.1.4.28"), "unblob"),
            PathBuf::from("/out/RAX54Sv2-V1.1.4.28.unblob.log")
        );
    }

    #[test]
    fn extractor_log_path_preserves_directory() {
        assert_eq!(
            extractor_log_path(Path::new("/out/dir/firmware"), "binwalkv3"),
            PathBuf::from("/out/dir/firmware.binwalkv3.log")
        );
    }
}
