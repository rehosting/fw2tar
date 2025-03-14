mod extractors;
mod args;

fn main() {
    println!("Available extractors:");

    for name in extractors::all_extractor_names() {
        println!("- {}", name);
    }
}
