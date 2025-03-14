use super::{ExtractError, Extractor};
use std::path::Path;
use std::process::Command;

pub struct Binwalk3Extractor;

impl Extractor for Binwalk3Extractor {
    fn name(&self) -> &'static str {
        "binwalkv3"
    }

    fn extract(
        &self,
        in_file: &Path,
        extract_dir: &Path,
        log_file: &Path,
    ) -> Result<(), ExtractError> {
        // TODO: reimplement using binwalk Rust API? Currently a lot of logic in
        //       binwalk 3.1's main.rs I'd need to reimplement...
        let output = Command::new("binwalk")
            .arg("-eM")
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
