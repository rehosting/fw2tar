use criterion::{criterion_group, criterion_main, Criterion};
use fw2tar::analysis::find_linux_filesystems::KEY_DIRS;
use std::cmp::{Ord, Ordering};
use std::ops::Deref;
use std::path::Path;

fn find_key_dirs_intersection() -> usize {
    let root = Path::new("/");
    let mut key_dir_count = 0;

    let mut dir_names: Vec<_> = root
        .read_dir()
        .unwrap()
        .map(|entry| {
            let entry = entry.unwrap();

            entry.file_name().to_string_lossy().into_owned()
        })
        .collect();

    dir_names.sort();

    let key_dirs_len = KEY_DIRS.len();
    let dir_names_len = dir_names.len();

    let mut key_i = 0;
    let mut dirs_i = 0;

    while key_i < key_dirs_len && dirs_i < dir_names_len {
        match dir_names[dirs_i].deref().cmp(KEY_DIRS[key_i]) {
            Ordering::Less => {
                dirs_i += 1;
            }
            Ordering::Equal => {
                key_dir_count += 1;
                dirs_i += 1;
                key_i += 1;
            }
            Ordering::Greater => {
                key_i += 1;
            }
        }
    }

    //assert_eq!(key_dir_count, KEY_DIRS.len());

    key_dir_count
}

fn find_key_dirs_filesystem() -> usize {
    let root = Path::new("/");
    let mut key_dir_count = 0;

    for dir in KEY_DIRS {
        if root.join(dir).is_dir() {
            key_dir_count += 1;
        }
    }

    //assert_eq!(key_dir_count, KEY_DIRS.len());

    key_dir_count
}

pub fn criterion_benchmark(c: &mut Criterion) {
    c.bench_function("find key dirs by interesection", |b| {
        b.iter(find_key_dirs_intersection)
    });

    c.bench_function("find key dirs by filesystem", |b| {
        b.iter(find_key_dirs_filesystem)
    });
}

criterion_group!(benches, criterion_benchmark);
criterion_main!(benches);
