pub mod analysis;
pub mod archive;
pub mod args;
mod error;
pub mod extractors;
pub mod metadata;

use analysis::{extract_and_process, extractor_log_path, ExtractionResult};
pub use error::Fw2tarError;
use metadata::{Manifest, Metadata, SecondaryFilesystem};

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

/// Path for a secondary root-like filesystem (index >= 1) when `--primary-limit`
/// is raised above 1: `<base>.<index>.rootfs.tar.gz`, sitting next to the primary
/// `<base>.rootfs.tar.gz`. Some firmware splits its rootfs across several
/// filesystems (e.g. a main image plus an `/opt` image); a consumer can mount
/// these where the device expects them.
fn secondary_archive_path(output_base: &Path, index: usize) -> PathBuf {
    let file_name = output_base.file_name().unwrap().to_string_lossy();
    output_base.with_file_name(format!("{file_name}.{index}.rootfs.tar.gz"))
}

/// Manifest descriptor for the secondary filesystem at `index`: its archive
/// filename relative to the primary (they share a directory, so the bare file
/// name is the relative path). Consumers resolve it against the primary's own
/// location rather than hardcoding it.
fn secondary_descriptor(output_base: &Path, index: usize) -> SecondaryFilesystem {
    let archive = secondary_archive_path(output_base, index)
        .file_name()
        .unwrap()
        .to_string_lossy()
        .into_owned();
    SecondaryFilesystem { index, archive }
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
    let winning_extractor = best_result.extractor;

    // Copy (not rename) the winner to the stable `<base>.rootfs.tar.gz` name so
    // its per-candidate `<base>.<extractor>.<index>.tar.gz` archive stays in
    // place. fw2tar's long-standing front-facing contract is one archive per
    // extractor per candidate filesystem (`<base>.{binwalk,unblob}.*.tar.gz`);
    // consumers still rely on those names, so they are kept. The
    // `<base>.rootfs.tar.gz` winner is an additional convenience on top.
    fs::copy(&best_result.path, &selected_output_path)
        .map_err(|e| Fw2tarError::OutputWrite(selected_output_path.clone(), e))?;

    // Secondary filesystems (index >= 1) from the SAME winning extractor: when
    // `--primary-limit` is raised, some firmware splits its rootfs across several
    // images (e.g. a main image plus an `/opt` image). Copy those to
    // `<base>.<index>.rootfs.tar.gz` so a consumer can mount them; they are tied
    // to the winning extractor so the set is internally consistent. Record the
    // ones that actually landed so the primary manifest can advertise the full
    // set (issue #70). Their own sidecars are written after.
    let mut secondary_filesystems = Vec::new();
    let mut secondary_sidecars = Vec::new();
    for sec in results
        .iter()
        .filter(|res| res.extractor == winning_extractor && res.index >= 1)
    {
        let sec_path = secondary_archive_path(&output, sec.index);
        match fs::copy(&sec.path, &sec_path) {
            Ok(_) => {
                eprintln!(
                    "fw2tar: secondary filesystem #{i} ({extractor}), archive at {path:?}",
                    i = sec.index,
                    extractor = winning_extractor,
                    path = sec_path,
                );
                secondary_filesystems.push(secondary_descriptor(&output, sec.index));
                secondary_sidecars.push((sec.manifest.clone(), sec_path));
            }
            Err(e) => log::warn!("failed to keep secondary filesystem {:?}: {e}", sec.path),
        }
    }
    secondary_filesystems.sort_by_key(|fs| fs.index);

    // The primary manifest advertises the secondary set. Built after the copies
    // above so it only lists filesystems that actually landed on disk.
    let mut primary_manifest = best_result.manifest.clone();
    primary_manifest.secondary_filesystems = secondary_filesystems;
    write_manifest_sidecar(&primary_manifest, &selected_output_path);

    // The primary was copied from the winning candidate, so its embedded trailer
    // still carries that candidate's original (secondary-free) manifest. When we
    // added secondaries, reconcile the trailer with the sidecar so the two never
    // diverge — the manifest is the same whether a consumer reads it from the
    // gzip trailer or the sidecar. No-op when there are no secondaries (the copy
    // already matches).
    if !primary_manifest.secondary_filesystems.is_empty() {
        if let Err(e) = archive::append_manifest_trailer(&selected_output_path, &primary_manifest) {
            log::warn!("failed to reconcile primary manifest trailer: {e}");
        }
    }

    for (manifest, sec_path) in &secondary_sidecars {
        write_manifest_sidecar(manifest, sec_path);
    }

    // The per-candidate `<base>.<extractor>.<index>.tar.gz` archives are part of
    // the output contract and are deliberately kept. Only the per-extractor logs
    // are intermediate scratch to sweep up on success.
    cleanup_extractor_logs(&extractors, &output, args.loud);

    Ok((result, selected_output_path))
}

/// Remove the per-extractor `<base>.<extractor>.log` files left next to the
/// output after a successful run (unless `--loud`). The per-candidate
/// `<base>.<extractor>.<index>.tar.gz` archives are part of fw2tar's output
/// contract and are deliberately preserved. Best-effort: failures are logged,
/// never fatal.
fn cleanup_extractor_logs(requested_extractors: &[String], output_base: &Path, loud: bool) {
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

    #[test]
    fn secondary_archive_path_inserts_index_before_rootfs_suffix() {
        // Secondary filesystems sit next to the primary, distinguished by index,
        // and (like the primary) must not greedily strip dotted version segments.
        assert_eq!(
            secondary_archive_path(Path::new("/out/dir/firmware"), 1),
            PathBuf::from("/out/dir/firmware.1.rootfs.tar.gz")
        );
        assert_eq!(
            secondary_archive_path(Path::new("RAX54Sv2-V1.1.4.28"), 2),
            PathBuf::from("RAX54Sv2-V1.1.4.28.2.rootfs.tar.gz")
        );
    }

    #[test]
    fn secondary_descriptor_records_relative_archive_name() {
        // The descriptor stored in the primary manifest must be the archive's
        // name relative to the primary (bare basename), not an absolute path,
        // and must preserve dotted version segments.
        let d = secondary_descriptor(Path::new("/out/dir/firmware"), 1);
        assert_eq!(d.index, 1);
        assert_eq!(d.archive, "firmware.1.rootfs.tar.gz");

        let d = secondary_descriptor(Path::new("RAX54Sv2-V1.1.4.28"), 2);
        assert_eq!(d.index, 2);
        assert_eq!(d.archive, "RAX54Sv2-V1.1.4.28.2.rootfs.tar.gz");
    }
}
