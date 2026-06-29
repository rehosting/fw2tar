use std::fs::{self, File};
use std::io::{self, Cursor, Write};
use std::iter;
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;

use flate2::write::GzEncoder;
use flate2::Compression;
use nix::unistd::{Gid, Group, Uid, User};
use serde::{Deserialize, Serialize};
use walkdir::{DirEntry, WalkDir};

use crate::metadata::{Manifest, Metadata};

const FIXED_TIMESTAMP: u64 = 1546318800; // Tue Jan 01 2019 05:00:00 GMT+0000

/// Trailing magic that marks an fw2tar manifest. Always the last 16 bytes of
/// the decompressed gzip stream, so a reader can locate the manifest from the
/// end regardless of tar size. Must stay exactly 16 bytes.
pub const MANIFEST_MAGIC: &[u8; 16] = b"made with fw2tar";

/// Version of the trailer *framing* (distinct from the manifest schema version).
const TRAILER_FRAME_VERSION: u16 = 1;

static BAD_PREFIXES: &[&str] = &["0.tar", "squashfs-root"];
// Extractor recursive-unpack directories created *in place* next to the file
// they came from. unblob uses `<name>_extract`; binwalk v3 uses `<name>.extracted`
// (and `decompressed.bin` chunks beneath it). These are not part of the firmware
// and must never ride along into the output archive.
static BAD_SUFFIXES: &[&str] = &["_extract", ".extracted", ".uncompressed", ".unknown"];

/// True when a path component looks like an extractor artifact (an intermediate
/// carve/unpack directory) that should be excluded from the output archive.
fn is_extraction_artifact(name: &str) -> bool {
    BAD_PREFIXES.iter().any(|prefix| name.starts_with(prefix))
        || BAD_SUFFIXES.iter().any(|suffix| name.ends_with(suffix))
}

/// Character or block special file. Serialized as "char"/"block".
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum DeviceKind {
    Char,
    Block,
}

/// The device kind for a node, else None for non-device file types.
fn device_kind(ft: fs::FileType) -> Option<DeviceKind> {
    if ft.is_char_device() {
        Some(DeviceKind::Char)
    } else if ft.is_block_device() {
        Some(DeviceKind::Block)
    } else {
        None
    }
}

/// A device node dropped from the archive payload. Penguin deliberately does
/// not want raw char/block nodes in the rehosted filesystem (it recreates them
/// with `mknod` post-extraction), so fw2tar strips them — but recording type +
/// major/minor + mode in the manifest makes that strip non-lossy: a downstream
/// consumer can replay them.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RemovedDevice {
    /// Path inside the rootfs, e.g. "/dev/console".
    pub path: String,
    pub kind: DeviceKind,
    pub major: u64,
    pub minor: u64,
    /// Permission bits (incl. setuid/setgid/sticky), e.g. 0o600.
    pub mode: u32,
}

pub fn tar_fs(
    rootfs_dir: &Path,
    tar_path: &Path,
    fw2tar_metadata: &Metadata,
    extractor_name: &str,
) -> io::Result<(usize, Manifest)> {
    let mut tar_entry_count = 0;
    let prefix_to_skip = rootfs_dir.components().count();
    let removed_devices: Mutex<Vec<RemovedDevice>> = Mutex::new(Vec::new());

    let should_add_to_tar = |entry: &DirEntry| {
        if entry.path() == rootfs_dir {
            return true;
        }

        if let Ok(meta) = entry.metadata() {
            if let Some(kind) = device_kind(meta.file_type()) {
                let path: PathBuf = iter::once(Component::RootDir)
                    .chain(entry.path().components().skip(prefix_to_skip))
                    .collect();
                let rdev = meta.rdev();
                removed_devices.lock().unwrap().push(RemovedDevice {
                    path: path.to_string_lossy().into_owned(),
                    kind,
                    major: libc::major(rdev) as u64,
                    minor: libc::minor(rdev) as u64,
                    mode: meta.permissions().mode() & 0o7777,
                });
                return false;
            }
        }

        entry
            .path()
            .file_name()
            .and_then(|name| name.to_str())
            .map(|name| !is_extraction_artifact(name))
            .unwrap_or(true)
    };

    let file = File::create(tar_path)?;
    let encoder = GzEncoder::new(file, Compression::default());

    let mut tar = tar::Builder::new(encoder);

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

    let manifest = Manifest::new(
        fw2tar_metadata.clone(),
        extractor_name,
        removed_devices.into_inner().unwrap(),
    );

    write_manifest_trailer(&mut encoder, &manifest)?;

    Ok((tar_entry_count, manifest))
}

/// Append the versioned manifest trailer to the gzip stream, after the tar's
/// EOF blocks. Layout (in the *decompressed* view, written last in the stream):
///
/// ```text
/// [ manifest JSON bytes ]
/// [ u32 little-endian: len(JSON) ]
/// [ u16 little-endian: TRAILER_FRAME_VERSION ]
/// [ 16-byte MANIFEST_MAGIC ]
/// ```
///
/// A reader gunzips the whole stream, checks the final 16 bytes against the
/// magic, then reads the frame version and JSON length backwards from there.
/// This is robust to tar size and to the trailer living in a separate gzip
/// member (gzip concatenation decompresses transparently).
pub fn write_manifest_trailer<W: Write>(writer: &mut W, manifest: &Manifest) -> io::Result<()> {
    let json = serde_json::to_vec(manifest)?;
    writer.write_all(&json)?;
    writer.write_all(&(json.len() as u32).to_le_bytes())?;
    writer.write_all(&TRAILER_FRAME_VERSION.to_le_bytes())?;
    writer.write_all(MANIFEST_MAGIC)?;
    Ok(())
}

/// Parse a manifest from the tail of a decompressed fw2tar stream (the inverse
/// of `write_manifest_trailer`). Returns `None` if the magic is absent or the
/// framing is malformed.
pub fn parse_manifest_trailer(decompressed: &[u8]) -> Option<Manifest> {
    let magic_len = MANIFEST_MAGIC.len();
    // magic(16) + frame version(2) + json length(4)
    let header = magic_len + 2 + 4;
    if decompressed.len() < header {
        return None;
    }
    let (rest, magic) = decompressed.split_at(decompressed.len() - magic_len);
    if magic != MANIFEST_MAGIC {
        return None;
    }
    let len_start = rest.len() - 6;
    let _frame_version = u16::from_le_bytes([rest[len_start + 4], rest[len_start + 5]]);
    let json_len = u32::from_le_bytes([
        rest[len_start],
        rest[len_start + 1],
        rest[len_start + 2],
        rest[len_start + 3],
    ]) as usize;
    let json_end = len_start;
    let json_start = json_end.checked_sub(json_len)?;
    serde_json::from_slice(&rest[json_start..json_end]).ok()
}

#[cfg(test)]
mod tests {
    use super::{
        is_extraction_artifact, parse_manifest_trailer, write_manifest_trailer, DeviceKind,
        RemovedDevice,
    };
    use crate::metadata::{Manifest, Metadata, MANIFEST_VERSION};

    fn sample_manifest() -> Manifest {
        Manifest::new(
            Metadata {
                input_hash: "abc123".into(),
                file: "firmware.bin".into(),
                fw2tar_command: vec!["fw2tar".into(), "firmware.bin".into()],
            },
            "unblob",
            vec![RemovedDevice {
                path: "/dev/console".into(),
                kind: DeviceKind::Char,
                major: 5,
                minor: 1,
                mode: 0o600,
            }],
        )
    }

    #[test]
    fn manifest_trailer_round_trips_from_the_end() {
        // Simulate the decompressed stream: arbitrary leading bytes (the tar)
        // followed by the trailer. Parsing must recover the manifest from the
        // tail without knowing where the tar ends.
        let mut buf: Vec<u8> = vec![0u8; 4096]; // stand-in for tar payload + EOF blocks
        write_manifest_trailer(&mut buf, &sample_manifest()).unwrap();

        let parsed = parse_manifest_trailer(&buf).expect("manifest should parse from the tail");
        assert_eq!(parsed.version, MANIFEST_VERSION);
        assert_eq!(parsed.extractor, "unblob");
        assert_eq!(parsed.input.input_hash, "abc123");
        assert_eq!(parsed.devices.len(), 1);
        assert_eq!(parsed.devices[0].path, "/dev/console");
        assert_eq!(parsed.devices[0].major, 5);
    }

    #[test]
    fn parse_manifest_trailer_rejects_missing_magic() {
        assert!(parse_manifest_trailer(b"not an fw2tar archive").is_none());
        assert!(parse_manifest_trailer(&[]).is_none());
    }

    #[test]
    fn removed_device_serializes_for_replay() {
        // The manifest must carry everything needed to recreate the node.
        let d = RemovedDevice {
            path: "/dev/console".into(),
            kind: DeviceKind::Char,
            major: 5,
            minor: 1,
            mode: 0o600,
        };
        let j: serde_json::Value =
            serde_json::from_slice(&serde_json::to_vec(&d).unwrap()).unwrap();
        assert_eq!(j["path"], "/dev/console");
        assert_eq!(j["kind"], "char");
        assert_eq!(j["major"], 5);
        assert_eq!(j["minor"], 1);
        assert_eq!(j["mode"], 0o600);
    }

    #[test]
    fn flags_bad_prefixes() {
        assert!(is_extraction_artifact("0.tar"));
        assert!(is_extraction_artifact("squashfs-root"));
        assert!(is_extraction_artifact("squashfs-root-0"));
    }

    #[test]
    fn flags_bad_suffixes() {
        assert!(is_extraction_artifact("firmware.bin_extract"));
        assert!(is_extraction_artifact("data.uncompressed"));
        assert!(is_extraction_artifact("blob.unknown"));
        // binwalk v3 names its in-place recursion dirs `<name>.extracted`.
        assert!(is_extraction_artifact("payload.tar.gz.extracted"));
    }

    #[test]
    fn keeps_real_rootfs_entries() {
        for name in ["bin", "etc", "usr", "sh", "passwd", "busybox", "libc.so.0"] {
            assert!(
                !is_extraction_artifact(name),
                "{name} should not be treated as an artifact"
            );
        }
    }
}
