#!/bin/bash
set -eu

# USAGE: ./unblob_package.sh firmware result_archive [tmp]

# First we run unblob with a temporary directory to extract the files
# Then we package potential rootfs files into tar.gz archives

firmware="$1"
output="$2"
# third argument is optional partition_dir
partition_dir=${3:-}

hashed_partitions_dir=$(mktemp -d)
extract_dir=$(mktemp -d) # If user specified a partition_dir, we'll move files from here later

# Stage 1: Run unblob on the provided firmware
log_scratch=$(mktemp) # Can't write unblob.log into /
unblob -k "$firmware" -e "$extract_dir" --log "${log_scratch}"
rm ${log_scratch}

# Clean unblob output - delete unblob artifacts and debian packages

# Delete all .uncompressed, .unknown, *.padding, and carved.elf files
find "$extract_dir" -type f \( -name '*.uncompressed' -o -name '*.unknown' -o -name '*.padding' -o -name 'carved.elf' \) -delete

# Find and delete debian packages - look for files named `debian_binary` that are in a directory named *_extract
# Also search for 'control' files that have a 'Package:' line and delete their parent directory
find "$extract_dir" -type f -name 'debian-binary' | while read -r debian_binary; do
    if [[ "$debian_binary" == *_extract/debian-binary ]]; then
        rm -rf "$(dirname "$debian_binary")"
    fi
done
find "$extract_dir" -type f -name 'control' | while read -r control_file; do
    if grep -q "^Package:" "$control_file"; then
        rm -rf "$(dirname "$control_file")"
    fi
done

# Archive all potential rootfs directories into a temporary directory
find $extract_dir -type d \( -name "*_carve" -o -name "*_extract" \) | while read -r dir; do
    # Create a name for the archive
    temp_archive="$hashed_partitions_dir/$(uuidgen).tar.gz"

    # Create the archive, excluding subdirectories ending with '_carve' '_extract' or '_uncompressed'
    # Also filter out ###-####.[ext] files, which are almost always unblob artifacts (e.g., 0-100.lzma)
    tar -czf "$temp_archive" \
        --exclude='*_carve' --exclude='*_extract' --exclude '*.uncompressed' \
        --exclude='[0-9]*-[0-9]*.*' \
        -C "$dir" .

    # If the generated archive has a size of 0, delete it. Need to run tar to get list of files
    if [ $(tar -tf "$temp_archive" | wc -l) -lt 2 ]; then
        rm "$temp_archive"
        continue
    fi
done

# Sort the archives by size and place them in the output directory, ordered by size
while read -r size file; do
    # Get the number of files in the archive
    nfiles=$(tar -tf "$file" | wc -l)

    # Generate a hash of the file contents
    file_hash=$(tar -xOf "$file" | sha256sum | cut -c1-8)

    # Create the new filename using file count and hash
    new_filename="${file_hash}.tar.gz"

    # Move and rename the file
    mv "$file" "$hashed_partitions_dir/$new_filename"

    echo "Packaged $new_filename (size: $size, nfiles: $nfiles)"
done < <(find "$hashed_partitions_dir" -type f -name "*.tar.gz" -print0 | xargs -0 du -s | sort -rn)

# If we have a partition dir, move the files there
if [ -n "$partition_dir" ]; then
    mv "$hashed_partitions_dir"/* "$partition_dir"
    hashed_partitions_dir="$partition_dir"
fi

# Now call unify
python3 -m unifyroot.cli "$hashed_partitions_dir" "$output" "$extract_dir"

# Always delete extract dir
rm -rf "$extract_dir"

# Delete partition dir if user didn't speciify one
if [ ! -n "$partition_dir" ]; then
    rm -rf "$hashed_partitions_dir"
fi