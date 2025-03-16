use clap::Parser;
use fw2tar::args::Args;
use std::process::exit;

fn main() {
    let args = Args::parse();

    pretty_env_logger::init_custom_env("FW2TAR_LOG");

    if let Err(e) = fw2tar::main(args) {
        eprintln!("{e}");
        exit(1);
    }
}
