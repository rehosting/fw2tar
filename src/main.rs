use std::process::exit;

use clap::Parser;

use fw2tar::args::Args;
use fw2tar::BestExtractor;

fn main() {
    let args = Args::parse();

    pretty_env_logger::init_custom_env("FW2TAR_LOG");

    let output_path = args
        .output
        .clone()
        .unwrap_or_else(|| args.firmware.with_extension(""))
        .with_extension(".rootfs.tar.gz");

    match fw2tar::main(args) {
        Ok(res) => match res {
            BestExtractor::Best(extractor) => {
                println!("Best extractor: {extractor}, archive at {output_path:?}");
            }
            BestExtractor::Only(extractor) => {
                println!("Only extractor: {extractor}, archive at {output_path:?}");
            }
            BestExtractor::Identical(extractor) => {
                println!("Extractors Identical, using {extractor}. Archive at {output_path:?}");
            }
            BestExtractor::None => {
                println!("No extractor succeeded.");
                exit(2);
            }
        },
        Err(e) => {
            eprintln!("{e}");
            exit(1);
        }
    }
}
