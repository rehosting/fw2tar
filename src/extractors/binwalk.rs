use super::{get_timeout, ExtractError, Extractor};
use std::path::Path;
use std::process::{Command, Stdio};

use wait_timeout::ChildExt;

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
        verbose: bool,
    ) -> Result<(), ExtractError> {
        let mut child = Command::new("python3")
            .args(&["-m", "binwalk"])
            .args(&["--run-as=root", "--preserve-symlinks", "-eM"])
            .arg("--log")
            .arg(log_file)
            .arg("-q")
            .arg(in_file)
            .arg("-C")
            .arg(extract_dir)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::null())
            .spawn()?;

        let timed_out = child.wait_timeout(get_timeout())?.is_none();
        if timed_out {
            log::warn!("binwalk timed out. Use `--timeout` to let it run longer.");
            child.kill()?;
        }

        let output = child.wait_with_output()?;

        self.cmd_output_to_result(output, timed_out, verbose)
    }
}
