pub mod analysis;
pub mod archive;
pub mod args;
mod error;
pub mod extractors;
pub mod metadata;

use analysis::{extract_and_process, extractor_log_path, ExtractionResult};
pub use error::Fw2tarError;
use metadata::{Manifest, Metadata};

use std::cmp::Reverse;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::{env, fs, thread};

pub enum BestExtractor {
    Best(&'static str),
    Only(&'static str),
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

    let extractors: Vec<String> = args
        .extractors
        .map(|extractors| extractors.split(",").map(String::from).collect())
        .unwrap_or_else(|| {
            extractors::all_extractor_names()
                .map(String::from)
                .collect()
        });

    eprintln!(
        "fw2tar: extracting {} with {} extractor(s): {}",
        args.firmware.display(),
        extractors.len(),
        extractors.join(", ")
    );

    let results: Mutex<Vec<ExtractionResult>> = Mutex::new(Vec::new());

    thread::scope(|threads| -> Result<(), Fw2tarError> {
        for extractor_name in &extractors {
            let extractor = extractors::get_extractor(extractor_name)
                .ok_or_else(|| Fw2tarError::InvalidExtractor(extractor_name.clone()))?;

            threads.spawn(|| {
                if let Err(e) = extract_and_process(
                    extractor,
                    &args.firmware,
                    &output,
                    args.scratch_dir.as_deref(),
                    args.loud,
                    args.primary_limit,
                    &results,
                    &metadata,
                ) {
                    log::info!("{} error: {e}", extractor.name());
                }
            });
        }

        Ok(())
    })?;

    let results = results.lock().unwrap();
    let mut best_results: Vec<_> = results.iter().filter(|&res| res.index == 0).collect();

    if best_results.is_empty() {
        return Ok((BestExtractor::None, selected_output_path));
    }

    let result = if best_results.len() == 1 {
        BestExtractor::Only(best_results[0].extractor)
    } else {
        eprintln!(
            "fw2tar: comparing {} candidate archives to pick the best extraction",
            best_results.len()
        );
        best_results.sort_by_key(|res| Reverse((res.file_node_count, res.extractor == "unblob")));
        BestExtractor::Best(best_results[0].extractor)
    };

    let best_result = best_results[0];

    fs::rename(&best_result.path, &selected_output_path)
        .map_err(|e| Fw2tarError::OutputWrite(selected_output_path.clone(), e))?;

    write_manifest_sidecar(&best_result.manifest, &selected_output_path);

    // The winning archive was renamed to `selected_output_path`; everything else
    // produced during the run (losing candidate tarballs, per-extractor logs) is
    // intermediate scratch the user did not ask for. Sweep it up on success.
    cleanup_stray_artifacts(&results, &extractors, &output, args.loud);

    Ok((result, selected_output_path))
}

/// Remove intermediate files left next to the output after a successful run:
/// the non-selected candidate `*.tar.gz` archives (the winner has already been
/// renamed away from its candidate path) and, unless running `--loud`, the
/// per-extractor `*.log` files. Best-effort: failures are logged, never fatal.
fn cleanup_stray_artifacts(
    results: &[ExtractionResult],
    requested_extractors: &[String],
    output_base: &Path,
    loud: bool,
) {
    for res in results {
        if res.path.exists() {
            if let Err(e) = fs::remove_file(&res.path) {
                log::warn!("failed to remove intermediate archive {:?}: {e}", res.path);
            }
        }
    }

    if loud {
        return;
    }

    for extractor in requested_extractors {
        let log_path = extractor_log_path(output_base, extractor);
        if log_path.exists() {
            if let Err(e) = fs::remove_file(&log_path) {
                log::warn!("failed to remove extractor log {log_path:?}: {e}");
            }
        }
    }
}

/// Write the chosen archive's manifest as a standalone `<archive>.manifest.json`
/// sidecar (the same content embedded in the archive's gzip trailer), and report
/// any device nodes that were stripped so the loss is never silent. The progress
/// notices go to stderr (always visible, unlike the level-gated logger); the
/// sidecar itself is best-effort — a failure to write it is logged, not fatal.
fn write_manifest_sidecar(manifest: &Manifest, output: &Path) {
    if !manifest.devices.is_empty() {
        let in_dev = manifest
            .devices
            .iter()
            .filter(|d| d.path.starts_with("/dev/"))
            .count();
        eprintln!(
            "fw2tar: stripped {} device node(s) from the rootfs ({in_dev} under /dev) — recorded in the manifest",
            manifest.devices.len(),
        );
    }

    let sidecar_path = {
        // Simple string append to avoid with_extension() being greedy.
        let file_name = output.file_name().unwrap().to_string_lossy();
        output.with_file_name(format!("{file_name}.manifest.json"))
    };
    match serde_json::to_vec_pretty(manifest) {
        Ok(bytes) => match fs::write(&sidecar_path, bytes) {
            Ok(()) => eprintln!("fw2tar: wrote manifest sidecar {}", sidecar_path.display()),
            Err(e) => log::warn!("failed to write manifest sidecar {sidecar_path:?}: {e}"),
        },
        Err(e) => log::warn!("failed to serialize manifest: {e}"),
    }
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
