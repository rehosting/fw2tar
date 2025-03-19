use std::fs::{self, File};
use std::io::{self, Cursor, Write};
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};

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

pub fn tar_fs(rootfs_dir: &Path, tar_path: &Path, fw2tar_metadata: &Metadata) -> io::Result<usize> {
    let mut tar_entry_count = 0;

    let should_add_to_tar = |entry: &DirEntry| {
        if entry.path() == rootfs_dir {
            return true;
        }

        if entry.metadata().map(is_blk_or_chr).unwrap_or(false) {
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

    let mut tar = tar::Builder::new(encoder);

    let prefix_to_skip = rootfs_dir.components().count();

    for entry in WalkDir::new(rootfs_dir)
        .into_iter()
        .filter_entry(should_add_to_tar)
    {
        let Ok(entry) = entry else { continue };
        let Ok(metadata) = entry.metadata() else {
            continue;
        };

        let rel_path: PathBuf = entry.path().components().skip(prefix_to_skip).collect();
        let entry_path = format!("./{}", rel_path.display());

        let entry_path = if !entry_path.ends_with('/') && metadata.is_dir() {
            format!("{entry_path}/")
        } else {
            entry_path
        };

        let data = if metadata.is_file() {
            fs::read(entry.path())?
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
            tar.append_link(
                &mut header,
                entry_path,
                fs::read_link(entry.path()).unwrap(),
            )?;
        } else {
            tar.append_data(&mut header, entry_path, Cursor::new(data))?;
        }
        tar_entry_count += 1;
    }

    tar.finish()?;

    let mut encoder = tar.into_inner()?;

    encoder.write_all(&[0; 0x10])?;

    let json_bytes = serde_json::to_vec(&fw2tar_metadata).unwrap();

    encoder.write_all(&json_bytes)?;
    encoder.write_all(b"made with fw2tar")?;

    Ok(tar_entry_count)
}
