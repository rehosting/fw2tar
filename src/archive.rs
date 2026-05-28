use std::collections::{BTreeMap, BTreeSet, HashSet, btree_set};
use std::fs::{self, File};
use std::io::{self, Cursor, Write};
use std::iter;
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::path::{Component,Path, PathBuf};
use std::sync::Mutex;

use flate2::write::GzEncoder;
use flate2::Compression;
use nix::unistd::{Gid, Group, Uid, User};
use walkdir::{DirEntry, WalkDir};

use crate::metadata::Metadata;

const FIXED_TIMESTAMP: u64 = 1546318800; // Tue Jan 01 2019 05:00:00 GMT+0000

static BAD_PREFIXES: &[&str] = &["0.tar", "squashfs-root"];
static BAD_SUFFIXES: &[&str] = &["_extract", ".uncompressed", ".unknown"];

fn is_blk_or_chr(meta: fs::Metadata) -> bool {
    meta.file_type().is_block_device() | meta.file_type().is_char_device()
}

pub fn tar_fs(
    paths: &BTreeMap<PathBuf,PathBuf>,
    tar_path: &Path,
    fw2tar_metadata: &Metadata,
    removed_devices: Option<&Mutex<HashSet<PathBuf>>>,
) -> io::Result<usize> {
    let mut tar_entry_count = 0;
    //log::debug!("paths: {:?}", paths);

    let should_add_to_tar = |entry: &DirEntry, start_dir: &Path, mount_point: &Path, count: usize| {
        if entry.path() == start_dir  {
            return true;
        }

        if entry.metadata().map(is_blk_or_chr).unwrap_or(false) {
            if let Some(removed_devices) = removed_devices {
                let path = mount_point.components()
                    .chain(entry.path().components().skip(count))
                    .collect();

                removed_devices.lock().unwrap().insert(path);
            }

            return false;
        }

        entry
            .path()
            .file_name()
            .map(|name| {
                name.to_str().map(|name| {
                    for prefix in BAD_PREFIXES {
                        if name.starts_with(prefix) {
                            return false;
                        }
                    }

                    for suffix in BAD_SUFFIXES {
                        if name.ends_with(suffix) {
                            return false;
                        }
                    }

                    true
                })
            })
            .flatten()
            .unwrap_or(true)
    };

    let file = File::create(tar_path)?;
    let encoder = GzEncoder::new(file, Compression::default());
    let mut paths_proven: BTreeSet<PathBuf> = BTreeSet::new();
    let mut tar = tar::Builder::new(encoder);
    for (mount_point, start_dir )in paths{
        let skip = start_dir.components().count();
        //make sure mount point exists
        mount_point.components().fold(PathBuf::from("./"),|mut path, component| {
            if component != Component::RootDir {
                path = path.join(component);
                if &path != mount_point && ! paths_proven.insert(path.clone()) {
                    let mut header = tar::Header::new_gnu();
                    header.set_mode(0o755);
                    tar.append_data(&mut header, format!("{}/",path.display()), Cursor::new(Vec::new())).unwrap();
                }
                path
            } else {
                path
            }
            
        });
        for entry in WalkDir::new(start_dir)
            .into_iter()
            .filter_entry(|a|should_add_to_tar(a, start_dir, mount_point, skip))
        {
            let Ok(entry) = entry else { continue };
            let Ok(metadata) = entry.metadata() else {
                continue;
            };

            let rel_path: PathBuf = mount_point.components().skip(1).chain(entry.path().components().skip(skip)).collect();
            let entry_path = format!("./{}", rel_path.display());

            let entry_path = if !entry_path.ends_with('/') && metadata.is_dir() {
                format!("{entry_path}/")
            } else {
                entry_path
            };
            //log::debug!("header: {:?},{:?},{:?},{:?}", entry_path,entry.path(),mount_point,start_dir);

            let data = if metadata.is_file() {
                let result = fs::read(entry.path());
                match result {
                    Err(a) => {log::debug!("{:?}",a); continue;},
                    Ok(a) => a,
                }
            } else {
                Vec::new()
            };

            let mut header = tar::Header::new_gnu();
            header.set_metadata_in_mode(&metadata, tar::HeaderMode::Deterministic);
            header.set_mode(metadata.permissions().mode());

            if entry_path == "./" {
                header.set_mode(0o755);
            }

            header.set_mtime(FIXED_TIMESTAMP);

            if metadata.is_file() {
                header.set_size(data.len() as u64); // buffering to prevent ToKToU
            }

            if let Ok(Some(user)) = User::from_uid(Uid::from_raw(metadata.uid())) {
                header.set_username(&user.name).unwrap();
            }

            if let Ok(Some(user)) = Group::from_gid(Gid::from_raw(metadata.gid())) {
                header.set_groupname(&user.name).unwrap();
            }

            header.set_cksum();
            if metadata.is_symlink() {
                //check if symlink would interfere with a mount point
                if let Some(_mount_point) = paths.keys().find(|mount_point: &&PathBuf| {
                    let root: PathBuf = iter::once(Component::RootDir).chain(rel_path.components()).collect();
                    mount_point.ancestors().any(|a| a.eq(&root))
                }){
                    header.set_entry_type(tar::EntryType::Directory);
                    log::debug!("root: {header:?}");
                    paths_proven.insert(PathBuf::from(entry_path.clone()));
                    tar.append_data(&mut header, format!("{entry_path}/"), Cursor::new(Vec::new()))?;
                } else {
                    tar.append_link(
                        &mut header,
                        entry_path,
                        fs::read_link(entry.path()).unwrap(),
                    )?;
                }

            } else {
                if metadata.is_dir() {
                    paths_proven.insert(PathBuf::from(entry_path.clone()));
                }
                tar.append_data(&mut header, entry_path, Cursor::new(data))?;
            }

            tar_entry_count += 1;
        }
    }

    tar.finish()?;

    let mut encoder = tar.into_inner()?;

    encoder.write_all(&[0; 0x10])?;

    let json_bytes = serde_json::to_vec(&fw2tar_metadata).unwrap();

    encoder.write_all(&json_bytes)?;
    encoder.write_all(b"made with fw2tar")?;

    Ok(tar_entry_count)
}
