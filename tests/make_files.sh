#!/bin/bash

# Make files in fs that look like a filesystem
mkdir -p "fs/etc"
mkdir -p "fs/dev"
mkdir -p "fs/home"
mkdir -p "fs/root"

# Directory where files will be created
target_dir="./fs/etc"

# Iterate over all permission combinations
for i in {0..7}; do
    for j in {0..7}; do
        for k in {0..7}; do
            # Form the permission string
            perm="$i$j$k"
            
            # Create a filename based on the permission
            filename="0o$perm"
            
            # Create an empty file with the specified name
            touch "$target_dir/$filename"
            
            # Apply the corresponding permissions to the file
            chmod "$perm" "$target_dir/$filename"
        done
    done
done

echo "Files with all possible permissions created in $target_dir."
sudo tar cvfz fs.tar.gz fs;
