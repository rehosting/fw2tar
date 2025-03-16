use fw2tar::analysis::{extract_and_process, ExtractionResult};
use fw2tar::extractors;
use fw2tar::metadata::Metadata;

use std::path::Path;
use std::sync::Mutex;

fn main() {
    pretty_env_logger::init_custom_env("FW2TAR_LOG");

    let results: Mutex<Vec<ExtractionResult>> = Mutex::new(Vec::new());

    extract_and_process(
        extractors::get_extractor("unblob").unwrap(),
        Path::new("../temp/fw2tar_test/rv130_archive.tar.gz"),
        Path::new("./rv130_test"),
        None,
        true,
        1,
        0,
        &results,
        &Metadata {
            input_hash: "insert input hash".into(),
            file: "rv130_archive.tar.gz".into(),
            fw2tar_command: vec!["early".into(), "test".into()],
        },
    )
    .unwrap();
}
