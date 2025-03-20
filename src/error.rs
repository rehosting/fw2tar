use std::path::PathBuf;

use thiserror::Error;

#[derive(Error, Debug)]
pub enum Fw2tarError {
    #[error("Invalid extractor {0:?} (valid options: binwalk, binwalkv3, unblob)")]
    InvalidExtractor(String),

    #[error("Provided firmware ({0:?}) is not a file")]
    FirmwareNotAFile(PathBuf),

    #[error("Provided firmware path ({0:?}) does not exist")]
    FirmwareDoesNotExist(PathBuf),
}
