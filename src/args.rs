use clap::Parser;
use std::path::PathBuf;

/// Convert firmware images into compressed tar archives
#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
pub struct Args {
    pub firmware: PathBuf,

    /// Scratch directory (optional). Default /tmp
    #[arg(long, alias("scratch_dir"))]
    pub scratch_dir: Option<PathBuf>,

    /// Output file base (optional). Default is firmware without extension. Final output will have .rootfs.tar.gz appended.
    #[arg(long)]
    pub output: Option<PathBuf>,

    /// Comma-separated list of extractors. Supported values are binwalk, binwalkv3, unblob
    #[arg(long)]
    pub extractors: Option<String>,

    /// Enable loud (verbose) output
    #[arg(long)]
    pub loud: bool,

    /// Maximum number of root-like filesystems to extract.
    #[arg(long, default_value_t = 1, alias("primary_limit"))]
    pub primary_limit: usize,

    /// Overwrite existing output file
    #[arg(long)]
    pub force: bool,

    /// Show help message for the wrapper script
    #[arg(long)]
    pub wrapper_help: bool,

    /// Timeout for extractors, measured in seconds
    #[arg(long, default_value_t = 20)]
    pub timeout: u64,
}
