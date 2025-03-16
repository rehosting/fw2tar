pub mod analysis;
pub mod archive;
pub mod args;
mod error;
pub mod extractors;
pub mod metadata;

use analysis::{extract_and_process, ExtractionResult};
use metadata::Metadata;

use std::env;
use std::sync::Mutex;
use std::thread;

pub use error::Fw2tarError;

pub fn main(args: args::Args) -> Result<(), Fw2tarError> {
    let metadata = Metadata {
        input_hash: analysis::sha1_file(&args.firmware).unwrap_or_default(),
        file: args.firmware.display().to_string(),
        fw2tar_command: env::args().collect(),
    };

    let extractors: Vec<_> = args
        .extractors
        .map(|extractors| extractors.split(",").map(String::from).collect())
        .unwrap_or_else(|| {
            extractors::all_extractor_names()
                .map(String::from)
                .collect()
        });

    let output = args
        .output
        .unwrap_or_else(|| args.firmware.with_extension(""));

    let results: Mutex<Vec<ExtractionResult>> = Mutex::new(Vec::new());

    thread::scope(|threads| -> Result<(), Fw2tarError> {
        for extractor in extractors {
            let extractor = extractors::get_extractor(&extractor)
                .ok_or_else(|| Fw2tarError::InvalidExtractor(extractor.clone()))?;

            let firmware = args.firmware.clone();
            let output = output.clone();
            let scratch_dir = args.scratch_dir.clone();
            let results = &results;
            let metadata = &metadata;

            threads.spawn(move || {
                extract_and_process(
                    extractor,
                    &firmware,
                    &output,
                    scratch_dir.as_deref(),
                    args.loud,
                    args.primary_limit,
                    args.secondary_limit,
                    results,
                    metadata,
                )
                .unwrap();
            });
        }

        Ok(())
    })?;

    Ok(())
}
