import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import stat

from collections import namedtuple, defaultdict, Counter
from pathlib import Path
from typing import Dict, List

from unblob.processing import ExtractionConfig, process_file
from unblob.logging import configure_logger
from unblob import report

from unifyroot import unify_filesystems

def sha1sum_file(filename):
    sha1  = hashlib.sha1() # XXX: if minimum python version >= 3.9, pass usedforsecurity=False
    buf  = bytearray(128*1024)
    bufview = memoryview(buf)
    with open(filename, 'rb', buffering=0) as f:
        for n in iter(lambda : f.readinto(bufview), 0):
            sha1.update(bufview[:n])
    return sha1.hexdigest()

def extract(filesystem: Path, outdir: Path):
    '''
    Given a filesystem blob, extract it into outdir. Return a dictionary
    mapping a unique ID for each extracted blob to (ID of source file, source file, extracted directory)
    Note that one ID may be an empty string (e.g., for the input file).
    '''
    configure_logger(0, outdir, Path('unblob.log'))
    unblob_results = process_file(
                                    ExtractionConfig(extract_root=outdir / 'unblob.root',
                                                    entropy_depth=0,
                                                    max_depth=100,
                                                    extract_suffix='_unblob_extracted', # Trying to be more unique than '_extracted'
                                                    verbose=False,
                                                    keep_extracted_chunks=True),
                                    filesystem)

    extractions = {} # chunk_id -> {'children': [ChunkIDs], 'paths': [Paths]}

    for task_result in unblob_results.results:
        # Todo: can we use the hash_reports? Do they correspond to this task? Seems like it's extraction hash, not input?
        #hash_report = [rpt for rpt in task_result.reports if isinstance(rpt, report.HashReport)]
        #if task_result.task.blob_id in extractions and len(hash_report):
        #    extractions[task_result.task.blob_id]['hash'] = hash_report[0].sha1
        for chunk_report in [x for x in task_result.reports if isinstance(x, report.ChunkReport)]:
            # For the current task (task_result.task.blob_id) we may have multiple chunks that were extracted
            # We'll record these (chunk_report.id) and the relationship to the parent task
            if chunk_report.id not in extractions:
                extractions[chunk_report.id] = {'input_file': str(task_result.task.path), 'parent': [], 'paths': [], 'ignore': False}
            if chunk_report.handler_name in ['elf32', 'elf64']:
                # Don't carve out ELFs
                extractions[chunk_report.id]['ignore'] = True

            extractions[chunk_report.id]['parent'].append(task_result.task.blob_id)

        for subtask in task_result.subtasks:
            # For the current task we'll have subtasks (subtask.blob_id) which are the extracted files that
            # will be processed. We'll capture the path for each.
            assert(subtask.blob_id in extractions), f"Missing blob ID in extractions: {subtask.blob_id}"

            # There should (maybe?) be exactly one path that ends with _unblob_extracted - that's the ONLY one we want
            if subtask.path.name.endswith('_unblob_extracted'):
                extractions[subtask.blob_id]['paths'].append(str(subtask.path))

            extractions[subtask.blob_id]['depth'] = subtask.depth

    return extractions

def package(extractions, output_dir):
    '''
    We want to create a clean set of directories in outdir.
    For each extraction we want to create
        outdir/extraacted/[sha1sum_of_blob].[unblob]/ and
        outdir/archives/[sha1sum_of_blob].[unblob].tar.gz
    '''
    # Create results directories. output/blobs/<sha1sum> and output/extracted/<sha1sum>.<extractor>
    blob_dir = output_dir / 'blobs'
    blob_dir.mkdir()

    extracted_dir = output_dir / 'extracted'
    extracted_dir.mkdir(exist_ok=True)

    archive_dir = output_dir / 'archives'
    archive_dir.mkdir(exist_ok=True)

    # Print results
    #for chunk_id, details in extractions.items():
    #    print(f"{chunk_id}")
    #    print(f"\tInput file: {details['input_file']}")
    #    print(f"\tExtraction produces paths:")
    #    for path in details['paths']:
    #        print(f"\t  - {path}")
    #    print(f"\tParent:")
    #    for child in details['parent']:
    #        print(f"\t  - {child}")

    # Copy each blob to the blob directory
    # TODO: many of these are just going to be elfs in the final FS.
    # Should we only copy blobs if we extract? But then what if we want to try alternative
    # extractors...
    for task_id, info in extractions.items():
        if info['ignore']:
            continue
        info['hash'] = sha1sum_file(info['input_file'])
        shutil.copy(info['input_file'], blob_dir / info['hash'])

    info = output_dir / 'info.json'
    with open(info, 'w') as f:
        f.write(json.dumps(extractions, indent=2))

    # Copy each extraction to the extracted directory
    # Identify all extraction directories that were created (so we don't copy later)
    extraction_dirs = set()
    seen_files = Counter() # hash -> count
    for task_id, info in extractions.items():
        for p in info['paths']:
            extraction_dirs.add(p)
        hsh = sha1sum_file(info['input_file'])
        info['input_hash'] = f"{hsh}.{seen_files[hsh]}"
        seen_files[hsh] += 1
    
    for task_id, info in extractions.items():
        if info['ignore']:
            continue
        out_dir = extracted_dir / f"{info['input_hash']}.unblob"

        if not len(info['paths']):
            # No extraction,
            continue
        subprocess.check_output(['cp', '-r', info['paths'][0], out_dir])

        # Now for each file in extraction_dirs (except info[1]), delete
        for extraction_dir in extraction_dirs:
            if extraction_dir in info['paths']:
                continue
            # Rewrite path to be relative to out_dir
            try:
                relative_path = Path(extraction_dir).relative_to(info['paths'][0])
            except ValueError:
                # Not a subpath, skip
                continue
            # Ensure output is not-empty and within the output directory
            if not len(str(out_dir / relative_path)) or not str(out_dir / relative_path).startswith(str(out_dir)):
                raise RuntimeError("Refusing to rm unsafe path", out_dir / relative_path)
            subprocess.check_output(['rm', '-rf', str(out_dir / relative_path)])

        # Now delete any directories in out_dir that end with '_unblob_extracted'
        for root, dirs, files in os.walk(out_dir):
            for d in dirs:
                if d.endswith('_unblob_extracted'):
                    subprocess.check_output(['rm', '-rf', os.path.join(root, d)])

    # Now archive each extraction
    for task_id, info in extractions.items():
        if info['ignore']:
            continue
        input_hash = info['input_hash']
        output = archive_dir / f"{input_hash}.unblob.tar.gz"
        in_dir = extracted_dir / f"{input_hash}.unblob"

        if not os.path.exists(in_dir):
            print(f"WARNING Skipping {in_dir} as it does not exist")
            continue

        subprocess.check_output(['tar', '-czf', str(output), '-C', str(in_dir), '.'])

def extract_and_package(firmware, output_dir):

    # Initial extraction
    extractions = extract(firmware, output_dir)

    package(extractions, output_dir)

def get_dir_size_exes(path):
    '''
    Recursively calculate the size of a directory.
    '''
    total_size, total_files, total_executables = 0, 0, 0

    for entry in path.iterdir():
        if entry.is_file():
            total_files += 1
            if entry.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                total_executables += 1
            try:
                total_size += entry.stat().st_size
            except FileNotFoundError as e:
                print(f"Unexpected FileNotFoundError: {e}")
                continue
            except OSError as e:
                # Happens if there are too many symlinks
                print("Unexpected OSError: {e}")
                continue
            except PermissionError as e:
                # Happens if we can't read the file
                print("Unexpected PermissionError: {e}")
                continue
            except Exception as e:
                print(f"Unexpected error: {e}")
                continue

        elif entry.is_dir() and not entry.is_symlink():
            # We can't recurse into symlink directories because they could
            # take us out of the extract dir or make us go into a cycle.
            # But we do count them as files above because symlinks should
            # count as executables, i.e., /bin/sh -> /bin/bash
            (dir_sz, dir_files, dir_exe) = get_dir_size_exes(entry)
            total_size += dir_sz
            total_files += dir_files
            total_executables += dir_exe

    return (total_size, total_files, total_executables)

def find_linux_filesystems(start_dir, min_executables=10):
    key_dirs = {'bin', 'etc', 'lib', 'usr', 'var'}
    critical_files = {'bin/sh', 'etc/passwd'}
    min_required = (len(key_dirs) + len(critical_files)) // 2  # Minimum number of key dirs and files

    filesystems = defaultdict(lambda: {'score': 0, 'size': 0, 'path': '', 'nfiles': 0, 'executables': 0})

    # List subdirectories
    for root in [x for x in start_dir.iterdir() if x.is_dir()]:
        dirs = [str(x.relative_to(root)) for x in root.iterdir() if x.is_dir()]

        root_path = Path(root)
        matched_dirs = key_dirs.intersection(set(dirs))
        matched_files = set()
        for critical_file in critical_files:
            if (root_path / critical_file.split('/')[-1]).exists():
                matched_files.add(critical_file)

        total_matches = len(matched_dirs) + len(matched_files)
        if total_matches >= min_required:
            size, nfiles, executables = get_dir_size_exes(root_path)

            if executables < min_executables:
                print(f"Warning: {executables} executables < {min_executables} required on analysis of FS {root_path} with size {size:,}")
                continue
            filesystems[str(root_path)].update({'score': total_matches, 'size': size, 'nfiles': nfiles, 'path': str(root_path), 'executables': executables})

    ranked_filesystems = sorted(filesystems.values(), key=lambda x: (-x['executables'], -x['size'], -x['score']))

    for fs in ranked_filesystems:
        print(f"Found filesystem: {fs['path']} with {fs['nfiles']:,} files, {fs['size']:,} bytes, {fs['executables']} executables")

    return [(Path(fs['path']), fs['size'], fs['nfiles']) for fs in ranked_filesystems]

def find_symlinks(directory):
    """Find all symlinks in the given directory relative to the directory"""
    symlinks = []
    for root, _, files in os.walk(directory):
        for name in files:
            filepath = os.path.join(root, name)
            if os.path.islink(filepath):
                symlinks.append(Path(filepath).relative_to(directory))
    return symlinks

def find_file_in_filesystems(filesystems, target):
    """Find the target file in other filesystems."""
    for fs in filesystems:
        possible_path = os.path.join(fs, target.lstrip('/'))
        if os.path.exists(possible_path):
            return possible_path
    return None

def logical_resolve(path, source=None):
    """
    Resolve a path (in the form of a string or a pathlib.Path object) into its
    absolute form by logically handling '.' and '..' components.
    This function does not access the filesystem.
    If a source is provided, the path is resolved relative to the source path.
    """
    # Ensure path is a list of components if it's not already
    if isinstance(path, str):
        path = Path(path)
    parts = list(path.parts)

    if source is not None:
        # Ensure source is a list of components if it's not already
        if isinstance(source, str):
            source = Path(source)
        source_parts = list(source.parts)

        # Prepend the source path to the path to resolve
        parts = source_parts + parts

    resolved_parts = []
    for part in parts:
        if part == '..':
            if resolved_parts:
                resolved_parts.pop()  # Go up one directory level
        elif part not in ('', '.', '/'):
            resolved_parts.append(part)  # Add actual directory/file name

    # Reconstruct the path from the resolved parts
    resolved_path = Path(*resolved_parts)

    # Ensure the path is absolute
    if not path.is_absolute():
        resolved_path = Path("/") / resolved_path

    return resolved_path

if __name__ == '__main__':
    firmware = Path(sys.argv[1])
    if not firmware.exists():
        print(f"File {firmware} does not exist")
        os.exit(1)

    output_dir = Path(sys.argv[2])
    if output_dir.exists():
        print(f"Output directory {output_dir} already exists. Removing it.")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    extract_and_package(firmware, output_dir)

    # Now we extracted into output dir. Let's unify
    unify_filesystems(str(output_dir / "archives"), str(output_dir / 'unified.tar.gz'))