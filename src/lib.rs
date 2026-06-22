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
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::{env, fs, thread};

pub enum BestExtractor {
    Best(&'static str),
    Only(&'static str),
    Identical(&'static str),
    None,
}

/// Derive the default output base from the firmware path by stripping a single
/// trailing extension (mirrors Python's `Path.stem`). Falls back to the path
/// unchanged when there is no stem (e.g. dotfiles or a bare path).
fn default_output_base(firmware: &Path) -> PathBuf {
    if let Some(stem) = firmware.file_stem() {
        firmware.with_file_name(stem)
    } else {
        firmware.to_path_buf()
    }
}

/// Append the `.rootfs.tar.gz` suffix to an output base. Uses `with_file_name`
/// (string append) rather than `with_extension`, which would greedily strip an
/// existing dotted segment (e.g. version numbers like `V1.1.4.28`).
fn rootfs_archive_path(output_base: &Path) -> PathBuf {
    let file_name = output_base.file_name().unwrap().to_string_lossy();
    output_base.with_file_name(format!("{}.rootfs.tar.gz", file_name))
}

pub fn main(args: args::Args) -> Result<(BestExtractor, PathBuf), Fw2tarError> {
    if !args.firmware.is_file() {
        if args.firmware.exists() {
            return Err(Fw2tarError::FirmwareNotAFile(args.firmware));
        } else {
            return Err(Fw2tarError::FirmwareDoesNotExist(args.firmware));
        }
    }

    let output = args
        .output
        .unwrap_or_else(|| default_output_base(&args.firmware));

    let selected_output_path = rootfs_archive_path(&output);

    if selected_output_path.exists() && !args.force {
        return Err(Fw2tarError::OutputExists(selected_output_path));
    }

    let metadata = Metadata {
        input_hash: analysis::sha1_file(&args.firmware).unwrap_or_default(),
        file: args.firmware.display().to_string(),
        fw2tar_command: env::args().collect(),
    };

    extractors::set_timeout(args.timeout);

    let extractors: Vec<_> = args
        .extractors
        .map(|extractors| extractors.split(",").map(String::from).collect())
        .unwrap_or_else(|| {
            extractors::all_extractor_names()
                .map(String::from)
                .collect()
        });

    let results: Mutex<Vec<ExtractionResult>> = Mutex::new(Vec::new());

    let removed_devices: Option<Mutex<HashSet<PathBuf>>> =
        args.log_devices.then(|| Mutex::new(HashSet::new()));

    thread::scope(|threads| -> Result<(), Fw2tarError> {
        for extractor_name in extractors {
            let extractor = extractors::get_extractor(&extractor_name)
                .ok_or_else(|| Fw2tarError::InvalidExtractor(extractor_name.clone()))?;

            threads.spawn(|| {
                if let Err(e) = extract_and_process(
                    extractor,
                    &args.firmware,
                    &output,
                    args.scratch_dir.as_deref(),
                    args.loud,
                    args.primary_limit,
                    args.secondary_limit,
                    &results,
                    &metadata,
                    removed_devices.as_ref(),
                ) {
                    log::info!("{} error: {e}", extractor.name());
                }
            });
        }

        Ok(())
    })?;

    if let Some(removed_devices) = removed_devices {
        let mut removed_devices = removed_devices
            .into_inner()
            .unwrap()
            .into_iter()
            .map(|path| path.to_string_lossy().into_owned())
            .collect::<Vec<_>>();

        removed_devices.sort();

        if removed_devices.is_empty() {
            log::warn!("No device files were found during extraction, skipping writing log");
        } else {
            let devices_log_path = {
                // Simple string append to avoid with_extension() being greedy
                let file_name = output.file_name().unwrap().to_string_lossy();
                output.with_file_name(format!("{}.devices.log", file_name))
            };
            fs::write(
                devices_log_path,
                removed_devices.join("\n"),
            )
            .unwrap();
        }
    }

    let results = results.lock().unwrap();
    let mut best_results: Vec<_> = results.iter().filter(|&res| res.index == 0).collect();

    let result = if best_results.is_empty() {
        return Ok((BestExtractor::None, selected_output_path));
    } else if best_results.len() == 1 {
        Ok((BestExtractor::Only(best_results[0].extractor), selected_output_path.clone()))
    } else {
        best_results.sort_by_key(|res| Reverse((res.file_node_count, res.extractor == "unblob")));

        Ok((BestExtractor::Best(best_results[0].extractor), selected_output_path.clone()))
    };

    let best_result = best_results[0];

    fs::rename(&best_result.path, &selected_output_path).unwrap();

    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_output_base_strips_single_extension() {
        assert_eq!(
            default_output_base(Path::new("/fw/firmware.bin")),
            PathBuf::from("/fw/firmware")
        );
    }

    #[test]
    fn default_output_base_keeps_inner_dots() {
        // A single trailing extension is stripped; version dots are preserved.
        assert_eq!(
            default_output_base(Path::new("RAX54Sv2-V1.1.4.28.zip")),
            PathBuf::from("RAX54Sv2-V1.1.4.28")
        );
    }

    #[test]
    fn default_output_base_without_extension_is_unchanged() {
        assert_eq!(
            default_output_base(Path::new("/fw/firmware")),
            PathBuf::from("/fw/firmware")
        );
    }

    #[test]
    fn rootfs_archive_path_appends_suffix_without_greedy_strip() {
        // The full version string must survive in the archive name.
        assert_eq!(
            rootfs_archive_path(Path::new("RAX54Sv2-V1.1.4.28")),
            PathBuf::from("RAX54Sv2-V1.1.4.28.rootfs.tar.gz")
        );
    }

    #[test]
    fn rootfs_archive_path_preserves_directory() {
        assert_eq!(
            rootfs_archive_path(Path::new("/out/dir/firmware")),
            PathBuf::from("/out/dir/firmware.rootfs.tar.gz")
        );
    }
}
