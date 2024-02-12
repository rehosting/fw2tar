#!/usr/bin/env python3
import os
import sys

def check_files_and_symlinks(directory):
    expected_symlinks = {
        'bin/symlink_same_dir': 'busybox',
        'bin/symlink_same_dir2': 'busybox',
        'bin/symlink_to_symlink': 'symlink_same_dir',
        'bin/symlink_absolute': 'busybox',
        'bin/symlink_broken': 'non_existent_file',
        'sbin/symlink_up_to_busybox': '../bin/busybox',
        'sbin/symlink_extra_up_to_busybox': '../bin/busybox',
    }
    expected_files = [
        'bin/busybox',
        'usr/bin/dummy',
    ]

    errors = False

    # Check symlinks
    for symlink, target in expected_symlinks.items():
        symlink_path = os.path.join(directory, symlink)
        if os.path.islink(symlink_path):
            actual_target = os.readlink(symlink_path)
            if target.startswith('/'):
                expected_path = target
            else:
                expected_path = os.path.normpath(os.path.join(os.path.dirname(symlink_path), target))

            if os.path.isabs(actual_target) or target.startswith('/'):
                if actual_target != expected_path:
                    print(f"Warning: Symlink {symlink} points to {actual_target}, expected {target}")
                    errors = True
            else:
                actual_path = os.path.normpath(os.path.join(directory, actual_target))
                if not os.path.exists(expected_path) or not os.path.exists(actual_path):
                    if actual_target != target:
                        print(f"Warning: Symlink {symlink} points to {actual_target}, expected {target}")
                        errors = True
                elif not os.path.samefile(actual_path, expected_path):
                    print(f"Warning: Symlink {symlink} points to {actual_target}, expected {target}")
                    errors = True
        else:
            print(f"Warning: Symlink {symlink} is missing")
            errors = True

    # Check files
    for file in expected_files:
        file_path = os.path.join(directory, file)
        if not os.path.exists(file_path):
            print(f"Warning: File {file} is missing")
            errors = True

    if not errors:
        print("All files and symlinks are correct")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 check_cpio_results.py <directory>")
        sys.exit(1)
    
    check_files_and_symlinks(sys.argv[1])
