use super::{ExtractError, Extractor};
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;
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

        let timed_out = child.wait_timeout(Duration::from_secs(20))?.is_none();
        if timed_out {
            child.kill()?;
        }

        let output = child.wait_with_output()?;

        self.cmd_output_to_result(output, timed_out)
    }
}
