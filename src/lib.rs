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
use std::collections::{BTreeMap, HashSet};
use std::path::PathBuf;
use std::sync::Mutex;
use std::{env, fs, thread};

use crate::archive::tar_fs;

pub enum BestExtractor {
    Best(&'static str),
    Only(&'static str),
    Identical(&'static str),
    None,
}


fn extract_external_args(external: Option<Vec<String>>, scratch_dir: Option<&PathBuf>) -> Result<Option<BTreeMap<PathBuf,PathBuf>>, Fw2tarError> {

    if let Some(external) = external {

    if let Some(scratch_dir) = scratch_dir {
        let external_mapping: Result<BTreeMap<PathBuf,PathBuf>, Fw2tarError> = external.iter().map(|a| -> Result<(PathBuf,PathBuf), Fw2tarError> {
            if let Some((path_str,mount_point)) = a.split_once(":"){
                let external = scratch_dir.join(path_str);
                //log::debug!("external: {:?}, {:?}",external,env::current_dir());
                if external.exists(){
                    return Ok((PathBuf::from(mount_point),external));
                }
            }   
            Err(Fw2tarError::ExternalFailure(a.to_string()))
            
        }).collect();
         Ok(Some(external_mapping?))

    }else {
         Err(Fw2tarError::ExternalFailure("No scratch_dir".to_string()))
    }
    }else {
        Ok(None)
    }
}
pub fn main(args: args::Args) -> Result<(BestExtractor, PathBuf), Fw2tarError> {
    let external_paths = if args.external.is_some() {
        extract_external_args(args.external.clone(),args.scratch_dir.as_ref())?
    } else{
        None
    };
   let internals: Option<BTreeMap<PathBuf, String>>  = if let Some(internal) = args.internal{
       let internal_mapping: Result<BTreeMap<PathBuf, String>, Fw2tarError> = internal.iter().map(|a| -> Result<(PathBuf,String),Fw2tarError> {
            if let Some((search_string,mount_point)) = a.split_once(":"){
                Ok((PathBuf::from(mount_point),search_string.to_string()))
            }else{Err(Fw2tarError::ExternalFailure(a.to_string()))}
        }).collect();
        Some(internal_mapping?)
    } else {None};
    let external_only = external_paths.is_some() && internals.is_none();
    let results: Mutex<Vec<ExtractionResult>> = Mutex::new(Vec::new());

    let removed_devices: Option<Mutex<HashSet<PathBuf>>> =
        args.log_devices.then(|| Mutex::new(HashSet::new()));

    if !args.firmware.is_file() && !external_only {
        if args.firmware.exists() {
            return Err(Fw2tarError::FirmwareNotAFile(args.firmware));
        } else {
            return Err(Fw2tarError::FirmwareDoesNotExist(args.firmware));
        }
    }
    let output = args.output.unwrap_or_else(|| {
        // Use file_stem() which should behave like Python's Path.stem
        if let Some(stem) = args.firmware.file_stem() {
            args.firmware.with_file_name(stem)
        } else {
            // No stem available, use as-is
            args.firmware.clone()
        }
    });

    let selected_output_path = {
        // Simple string append to avoid with_extension() being greedy
        let file_name = output.file_name().unwrap().to_string_lossy();
        output.with_file_name(format!("{}.rootfs.tar.gz", file_name))
    };

    if selected_output_path.exists() && !args.force {
        return Err(Fw2tarError::OutputExists(selected_output_path));
    }

    let metadata = Metadata {
        input_hash: analysis::sha1_file(&args.firmware).unwrap_or_default(),
        file: args.firmware.display().to_string(),
        fw2tar_command: env::args().collect(),
    };

    extractors::set_timeout(args.timeout);
    //remove clone
    let extractors: Vec<_> = args
        .extractors
        .map(|extractors| extractors.split(",").map(String::from).collect())
        .unwrap_or_else(|| {
            extractors::all_extractor_names()
                .map(String::from)
                .collect()
        });

    if !external_only {
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
                    external_paths.as_ref(),
                    internals.as_ref(),
                ) {
                    log::info!("{} error: {e}", extractor.name());
                }
            });
        }

        Ok(())
    })?;
    }else {
        let result = tar_fs(&external_paths.unwrap(), &selected_output_path, &metadata, removed_devices.as_ref());
        //log::debug!("{:?}",result);
    }

   

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
            fs::write(devices_log_path, removed_devices.join("\n")).unwrap();
        }
    }
    
    let results = results.lock().unwrap();
    let mut best_results: Vec<_> = results.iter().filter(|&res| res.index == 0).collect();
    let result = if best_results.is_empty() {
        return Ok((BestExtractor::None, selected_output_path));
    } else if best_results.len() == 1 {
        Ok((
            BestExtractor::Only(best_results[0].extractor),
            selected_output_path.clone(),
        ))
    } else {
        best_results.sort_by_key(|res| Reverse((res.file_node_count, res.extractor == "unblob")));

        Ok((
            BestExtractor::Best(best_results[0].extractor),
            selected_output_path.clone(),
        ))
    };

    let best_result = best_results[0];

    fs::rename(&best_result.path, &selected_output_path).unwrap();

    result

}
