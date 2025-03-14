use std::io;
use std::os::unix::process::ExitStatusExt;
use std::path::Path;
use std::process::Output;
use thiserror::Error;

mod binwalk;
mod binwalk3;
mod unblob;

#[derive(Error, Debug)]
pub enum ExtractError {
    #[error("An I/O error occurred while attempting to extract ({0})")]
    Io(#[from] io::Error),

    #[error("Extraction process was killed with signal {0:?}")]
    Killed(Option<i32>),

    #[error("Extraction process exited with code {0}")]
    Failed(i32),
}

pub trait Extractor {
    const NAME: &'static str;

    fn extract(
        &self,
        in_file: &Path,
        extract_dir: &Path,
        log_file: &Path,
    ) -> Result<(), ExtractError>;

    fn cmd_output_to_result(output: Output) -> Result<(), ExtractError> {
        if output.status.success() {
            Ok(())
        } else {
            if let Some(code) = output.status.code() {
                log::error!("{} exited with error code {}", Self::NAME, code);
                Err(ExtractError::Failed(code))
            } else {
                Err(ExtractError::Killed(output.status.signal()))
            }
        }
    }
}
