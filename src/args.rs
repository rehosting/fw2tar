use clap::Parser;
use std::path::PathBuf;

/// Convert firmware images into compressed tar archives
#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
pub struct Args {
    pub firmware: PathBuf,

    /// Scratch directory (optional). Default /tmp
    #[arg(long)]
    pub scratch_dir: Option<PathBuf>,

    /// Output file base (optional). Default is firmware without extension.
    #[arg(long)]
    pub output: Option<PathBuf>,

    /// Comma-separated list of extractors. Supported values are binwalk, binwalkv3, unblob
    #[arg(long)]
    pub extractors: Option<String>,

    /// Enable loud (verbose) output
    #[arg(long)]
    pub loud: bool,

    /// Create a file next to the output file reporting the extractor used
    #[arg(long)]
    pub report_extractor: bool,

    /// Maximum number of root-like filesystems to extract.
    #[arg(long, default_value_t = 1)]
    pub primary_limit: usize,

    /// Maximum number of non-root-like filesystems to extract.
    #[arg(long, default_value_t = 0)]
    pub secondary_limit: usize,

    /// Overwrite existing output file
    #[arg(long)]
    pub force: bool,

    /// Show help message for the wrapper script
    #[arg(long)]
    pub wrapper_help: bool,

    /// Create a file showing all the devices removed from any of the extractions
    #[arg(long)]
    pub log_devices: bool,
}
