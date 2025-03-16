use std::fs::{self, File};
use std::io::{self, Cursor, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use flate2::write::GzEncoder;
use flate2::Compression;
use walkdir::{DirEntry, WalkDir};

use crate::metadata::Metadata;

const FIXED_TIMESTAMP: u64 = 1546318800; // Tue Jan 01 2019 05:00:00 GMT+0000

static BAD_PREFIXES: &[&str] = &["0.tar", "squashfs-root"];
static BAD_SUFFIXES: &[&str] = &["_extract", ".uncompressed", ".unknown"];

const IS_BLK_OR_CHR_MASK: u32 = libc::S_IFBLK | libc::S_IFCHR;

fn is_blk_or_chr(meta: fs::Metadata) -> bool {
    meta.is_file() && meta.permissions().mode() & IS_BLK_OR_CHR_MASK != 0
}

pub fn tar_fs(rootfs_dir: &Path, tar_path: &Path, fw2tar_metadata: &Metadata) -> io::Result<()> {
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

        let data = if metadata.is_file() {
            std::fs::read(entry.path())?
        } else {
            Vec::new()
        };

        let mut header = tar::Header::new_gnu();
        header.set_metadata_in_mode(&metadata, tar::HeaderMode::Deterministic);

        if entry_path == "./" {
            header.set_mode(0o755);
        }

        header.set_path(entry_path).unwrap();
        header.set_mtime(FIXED_TIMESTAMP);

        if metadata.is_file() {
            header.set_size(data.len() as u64); // buffering to prevent ToKToU
        }

        header.set_cksum();

        tar.append(&header, Cursor::new(data))?;
    }

    tar.finish()?;

    let mut encoder = tar.into_inner()?;

    encoder.write_all(&[0; 0x10])?;

    let json_bytes = serde_json::to_vec(&fw2tar_metadata).unwrap();

    encoder.write_all(&json_bytes)?;
    encoder.write_all(b"made with fw2tar")?;

    Ok(())
}
