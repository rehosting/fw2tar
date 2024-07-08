#!/bin/bash
set -eu

# USAGE: ./unblob_package.sh firmware output_root

#if [ "$#" -ne 2 ]; then
#    echo "Usage: $0 [firmware] [output_dir]"
#    exit 1
#fi


# First we run unblob with a temporary directory to extract the files
# Then we package potential rootfs files into tar.gz archives

firmware="$1"
output_dir="$2"
mkdir -p "$output_dir"
chmod 777 "$output_dir"

# Stage 1: Run unblob on the provided firmware
extract_dir=$(mktemp -d)
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
temp_dir=$(mktemp -d)
find $extract_dir -type d \( -name "*_carve" -o -name "*_extract" \) | while read -r dir; do
    # Create a name for the archive
    temp_archive="$temp_dir/$(uuidgen).tar.gz"

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
    mv "$file" "$output_dir/$new_filename"

    echo "Packaged $new_filename (size: $size, nfiles: $nfiles)"
done < <(find "$temp_dir" -type f -name "*.tar.gz" -print0 | xargs -0 du -s | sort -rn)

# Don't create root-owned files that end users can't delete in mapped directories
chmod 777 "${output_dir}/"*
rm -rf "$temp_dir"