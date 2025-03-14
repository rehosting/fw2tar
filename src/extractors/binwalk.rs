use super::{ExtractError, Extractor};
use std::path::Path;
use std::process::Command;

pub struct BinwalkExtractor;

impl Extractor for BinwalkExtractor {
    fn name(&self) -> &'static str {
        "binwalk"
    }

    fn extract(
        &self,
        in_file: &Path,
        extract_dir: &Path,
        log_file: &Path,
    ) -> Result<(), ExtractError> {
        let output = Command::new("python3")
            .args(&["-m", "binwalk"])
            .args(&["--run-as=root", "--preserve-symlinks", "-eM"])
            .arg("--log")
            .arg(log_file)
            .arg("-q")
            .arg(in_file)
            .arg("-C")
            .arg(extract_dir)
            .output()?;

        self.cmd_output_to_result(output)
    }
}
