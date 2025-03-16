use thiserror::Error;

#[derive(Error, Debug)]
pub enum Fw2tarError {
    #[error("Invalid extractor {0:?} (valid options: binwalk, binwalkv3, unblob)")]
    InvalidExtractor(String),
}
