use serde::{Deserialize, Serialize};

use crate::archive::RemovedDevice;

/// Current manifest schema version. Bump only on an *incompatible* change;
/// adding new optional fields is forward-compatible because readers ignore
/// unknown keys, so it does not require a bump.
pub const MANIFEST_VERSION: u32 = 1;

/// Identifying information about the input firmware and the invocation that
/// produced the archive. The field names (`file`, `input_hash`,
/// `fw2tar_command`) are part of the on-disk format and are kept stable.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Metadata {
    pub input_hash: String,
    pub file: String,
    pub fw2tar_command: Vec<String>,
}

/// Descriptor for a secondary root-like filesystem kept alongside the primary
/// when `--primary-limit` is raised above 1. Recorded in the *primary's*
/// manifest so a consumer can discover the full set of filesystems a run
/// produced — and reassemble them — from the primary manifest alone, without
/// hardcoding archive filenames per firmware.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SecondaryFilesystem {
    /// Candidate index (>= 1) assigned during extraction; index 0 is the primary.
    pub index: usize,
    /// Archive filename relative to the primary (same directory), e.g.
    /// `firmware.1.rootfs.tar.gz`.
    pub archive: String,
    // `mount` — the subpath within the primary where this filesystem is meant to
    // mount — is intentionally not recorded yet: fw2tar does not currently infer
    // it (see issue #70). Added later without a version bump, per the note above.
}

/// The versioned side-channel spec that fw2tar tacks onto its output: embedded
/// in the gzip trailer after the tar EOF blocks (see `archive::write_manifest_trailer`)
/// and also written verbatim as a `<archive>.manifest.json` sidecar. The tar
/// payload itself stays a plain, consistently-extractable archive; everything a
/// consumer (e.g. Penguin) needs to know *beyond* the payload lives here.
///
/// The input fields are flattened to the top level so the keys match the legacy
/// trailer layout that `utils/show_metadata.py` and `utils/stitch` rely on.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    /// Manifest schema version (`MANIFEST_VERSION`).
    pub version: u32,

    #[serde(flatten)]
    pub input: Metadata,

    /// Name of the extractor whose output was chosen (e.g. "unblob").
    pub extractor: String,

    /// Char/block device nodes stripped from the rootfs. Penguin recreates
    /// these with `mknod` after extraction rather than carrying raw nodes in
    /// the rehosted filesystem, so recording type + major/minor + mode makes
    /// the strip non-lossy.
    pub devices: Vec<RemovedDevice>,

    /// Secondary root-like filesystems this run produced from the *same*
    /// (winning) extractor, populated only on the primary's manifest when
    /// `--primary-limit` is raised above 1. Empty for the common single-rootfs
    /// case, and `skip_serializing_if` keeps it out of the emitted JSON then, so
    /// existing single-filesystem output is byte-identical.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub secondary_filesystems: Vec<SecondaryFilesystem>,
    // Reserved for a future iteration (forward-compatible per the note above):
    //   * `mounts` — where each secondary filesystem mounts in the primary.
    //     Deferred until fw2tar can infer the mount point (issue #70).
}

impl Manifest {
    pub fn new(input: Metadata, extractor: &str, devices: Vec<RemovedDevice>) -> Self {
        Self {
            version: MANIFEST_VERSION,
            input,
            extractor: extractor.to_string(),
            devices,
            secondary_filesystems: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_manifest() -> Manifest {
        Manifest::new(
            Metadata {
                input_hash: "abc123".into(),
                file: "firmware.bin".into(),
                fw2tar_command: vec!["fw2tar".into(), "firmware.bin".into()],
            },
            "unblob",
            Vec::new(),
        )
    }

    #[test]
    fn empty_secondary_filesystems_omitted_from_json() {
        // The common single-rootfs case must not gain a new key, so existing
        // consumers and golden output stay byte-identical.
        let json = serde_json::to_string(&sample_manifest()).unwrap();
        assert!(
            !json.contains("secondary_filesystems"),
            "empty secondary list must be omitted, got: {json}"
        );
    }

    #[test]
    fn populated_secondary_filesystems_round_trip() {
        let mut manifest = sample_manifest();
        manifest.secondary_filesystems = vec![
            SecondaryFilesystem {
                index: 1,
                archive: "firmware.1.rootfs.tar.gz".into(),
            },
            SecondaryFilesystem {
                index: 2,
                archive: "firmware.2.rootfs.tar.gz".into(),
            },
        ];

        let json = serde_json::to_string(&manifest).unwrap();
        assert!(json.contains("secondary_filesystems"));

        let parsed: Manifest = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.secondary_filesystems, manifest.secondary_filesystems);
    }

    #[test]
    fn manifest_without_secondary_key_deserializes() {
        // A manifest written before this field existed (no key) must still load,
        // defaulting to an empty list.
        let legacy = r#"{
            "version": 1,
            "input_hash": "abc123",
            "file": "firmware.bin",
            "fw2tar_command": ["fw2tar", "firmware.bin"],
            "extractor": "unblob",
            "devices": []
        }"#;
        let parsed: Manifest = serde_json::from_str(legacy).unwrap();
        assert!(parsed.secondary_filesystems.is_empty());
    }
}
