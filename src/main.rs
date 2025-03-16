use fw2tar::analysis::directory_executables::get_dir_executable_info;
//use fw2tar::extractors;

use std::path::Path;

fn main() {
    //println!("Available extractors:");

    //for name in extractors::all_extractor_names() {
    //    println!("- {}", name);
    //}

    let executable_info = get_dir_executable_info(Path::new("/"));

    dbg!(executable_info);
}
