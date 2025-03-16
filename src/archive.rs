use std::fs::File;
use std::io::{self, Cursor, Write};
use std::path::{Path, PathBuf};

use flate2::write::GzEncoder;
use flate2::Compression;
use walkdir::WalkDir;

use crate::metadata::Metadata;

const FIXED_TIMESTAMP: u64 = 1546318800; // Tue Jan 01 2019 05:00:00 GMT+0000

pub fn tar_fs(rootfs_dir: &Path, tar_path: &Path, fw2tar_metadata: &Metadata) -> io::Result<()> {
    let file = File::create(tar_path)?;
    let encoder = GzEncoder::new(file, Compression::default());

    let mut tar = tar::Builder::new(encoder);

    let prefix_to_skip = rootfs_dir.components().count();

    for entry in WalkDir::new(rootfs_dir) {
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
        header.set_path(entry_path).unwrap();
        header.set_mtime(FIXED_TIMESTAMP);

        dbg!(&header);

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
