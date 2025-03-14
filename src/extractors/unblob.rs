use super::{ExtractError, Extractor};
use std::path::Path;
use std::process::Command;

pub struct UnblobExtractor;

impl Extractor for UnblobExtractor {
    fn name(&self) -> &'static str {
        "unblob"
    }

    fn extract(
        &self,
        in_file: &Path,
        extract_dir: &Path,
        log_file: &Path,
    ) -> Result<(), ExtractError> {
        let output = Command::new("unblob")
            .arg(in_file)
            .arg("-e")
            .arg(extract_dir)
            .arg("--log")
            .arg(log_file)
            .args(&["--entropy-depth", "1"])
            .output()?;

        self.cmd_output_to_result(output)
    }
}
