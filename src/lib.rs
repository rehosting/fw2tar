pub mod analysis;
pub mod archive;
pub mod args;
mod error;
pub mod extractors;
pub mod metadata;

use analysis::{extract_and_process, ExtractionResult};
pub use error::Fw2tarError;
use metadata::Metadata;

use std::cmp::Reverse;
use std::sync::Mutex;
use std::{env, fs, thread};

pub enum BestExtractor {
    Best(&'static str),
    Only(&'static str),
    Identical(&'static str),
    None,
}

pub fn main(args: args::Args) -> Result<BestExtractor, Fw2tarError> {
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

            threads.spawn(|| {
                extract_and_process(
                    extractor,
                    &args.firmware,
                    &output,
                    args.scratch_dir.as_deref(),
                    args.loud,
                    args.primary_limit,
                    args.secondary_limit,
                    &results,
                    &metadata,
                )
                .unwrap();
            });
        }

        Ok(())
    })?;

    let results = results.lock().unwrap();
    let mut best_results: Vec<_> = results.iter().filter(|&res| res.index == 0).collect();

    let result = if best_results.is_empty() {
        return Ok(BestExtractor::None);
    } else if best_results.len() == 1 {
        Ok(BestExtractor::Only(best_results[0].extractor))
    } else {
        best_results.sort_by_key(|res| Reverse((res.file_node_count, res.extractor == "unblob")));

        Ok(BestExtractor::Best(best_results[0].extractor))
    };

    let best_result = best_results[0];

    fs::rename(&best_result.path, output.with_extension("rootfs.tar.gz")).unwrap();

    result
}
