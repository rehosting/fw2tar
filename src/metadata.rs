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
    // Reserved for future iterations (kept out of the emitted JSON until
    // populated; adding them later is forward-compatible per the note above):
    //   * `mounts` — where secondary filesystems would mount in the primary.
    //   * `secondary_filesystems` — descriptors for (or payloads of) additional
    //     candidate filesystems.
}

impl Manifest {
    pub fn new(input: Metadata, extractor: &str, devices: Vec<RemovedDevice>) -> Self {
        Self {
            version: MANIFEST_VERSION,
            input,
            extractor: extractor.to_string(),
            devices,
        }
    }
}
