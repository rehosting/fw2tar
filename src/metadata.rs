use serde::{Deserialize, Serialize};

/// Output archive metadata that is concatonated to the tar (inside the gzip)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Metadata {
    pub input_hash: String,
    pub file: String,
    pub fw2tar_command: Vec<String>,
}
