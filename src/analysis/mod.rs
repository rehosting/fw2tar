use std::path::Path;
use std::sync::Mutex;
use std::time::Instant;
use std::{env, io};

use tempfile::TempDir;
use thiserror::Error;

pub mod directory_executables;
pub mod find_linux_filesystems;

use crate::extractors::{ExtractError, Extractor};

struct ExtractionResult {
    extractor: &'static str,
    index: usize,
    size: usize,
    num_files: usize,
    primary: bool,
    archive_hash: String,
}

#[derive(Error, Debug)]
enum ExtractProcessError {
    #[error("Failed to create temporary directory ({0:?})")]
    TempDirFail(io::Error),

    #[error("Failed to extract from file with extractor ({0})")]
    ExtractFail(ExtractError),
}

pub fn extract_and_process(
    extractor: &dyn Extractor,
    in_file: &Path,
    out_file_base: &Path,
    scratch_dir: Option<&Path>,
    verbose: bool,
    primary_limit: usize,
    secondary_limit: usize,
    results: &Mutex<Vec<ExtractionResult>>,
) -> Result<(), ExtractProcessError> {
    let extractor_name = extractor.name();

    let scratch_dir = scratch_dir
        .map(Path::to_path_buf)
        .unwrap_or_else(env::temp_dir);

    let temp_dir_prefix = format!("fw2tar_{extractor_name}");
    let temp_dir = TempDir::with_prefix_in(temp_dir_prefix, &scratch_dir)
        .map_err(ExtractProcessError::TempDirFail)?;

    let extract_dir = temp_dir.path();

    let log_file = out_file_base.with_extension(format!("{extractor_name}.log"));

    let start_time = Instant::now();

    extractor
        .extract(in_file, extract_dir, &log_file)
        .map_err(ExtractProcessError::ExtractFail)?;

    let elapsed = start_time.elapsed().as_secs_f32();

    if verbose {
        println!("{extractor_name} took {elapsed:.2} seconds")
    } else {
        log::info!("{extractor_name} took {elapsed:.2} seconds");
    }

    todo!("find_linux_filesystems");

    drop(temp_dir);

    Ok(())
}
