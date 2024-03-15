import tarfile
import os

def parse_permissions(perm_int):
    '''
    Convert integer permission to a binary string representation split into four parts:
    special bits, user, group, and others permissions.
    '''
    # Convert to binary and ensure it's 12 bits (for special and standard permissions)
    bin_perm = bin(perm_int)[2:].zfill(12)
    # Split into special (first 3 bits) and standard permissions (remaining 9 bits)
    return bin_perm[:3], bin_perm[3:6], bin_perm[6:9], bin_perm[9:]

def compare_permissions(old_perm, new_perm):
    """Compare two permission sets including special bits and return the differences."""
    perms = ['r', 'w', 'x']
    types = ['s', 'u', 'g', 'o']  # Include special bits as 's'
    changes = []

    for i in range(4):  # Loop through 4 types (including special)
        for j in range(3):  # Loop through r, w, x
            # For the special bits, only compare if it's the first set (s)
            if i == 0 and j > 0:  # Skip checks for SUID and SGID bits in 's' type
                continue
            if old_perm[i][j] != new_perm[i][j]:
                change = '+' if new_perm[i][j] == '1' else '-'
                change_type = types[i] if i > 0 else ''  # Don't prefix changes for special bits
                perm_type = perms[j] if i > 0 else 't' if j == 0 else ''
                changes.append(f"{change_type}{change}{perm_type}")

    return ','.join(changes)

def permission_to_string(perm):
    '''
    Convert a permission list including special bits to a standard rwxrwxrwx string
    '''
    perms = ['r', 'w', 'x']
    special = ['', 's', 's', 't']  # Representations for SUID, SGID, and Sticky bit

    if isinstance(perm, int):
        perm = parse_permissions(perm)

    result = ''
    for i in range(1, 4):  # Skip the first set, which is special bits
        for j in range(3):
            result += perms[j] if perm[i][j] == '1' else '-'
            if i < 3 and j == 2:  # Check for SUID/SGID
                if perm[0][i-1] == '1':  # If special bit is set
                    result = result[:-1] + (perms[j].upper() if result[-1] == '-' else special[i])

    # Handle Sticky Bit separately
    if perm[0][2] == '1':  # If sticky bit is set
        if result[-1] == 'x':
            result = result[:-1] + special[3]
        else:
            result = result[:-1] + result[-1].upper()  # Use uppercase T if execute is not set

    return result

def combine_perms(perm_diff):
    '''
    if we have multiple diffs for the same u/g/o group, combine into a single string
    '''
    perms = {} # u/g/o -> [rwx]
    for d in perm_diff.split(","):
        target, change, perm = d[0], d[1], d[2:]
        perms[f"{target}{change}"] = perms.get(target, []) + [perm]

    # Now join together and return as string
    return ','.join([f"{target}{''.join(perm)}" for target, perm in perms.items()])

def permission_difference(old_octal, new_octal):
    """Generate a string describing the difference between two octal permissions."""
    if isinstance(old_octal, int):
        old_octal = parse_permissions(old_octal)
    if isinstance(new_octal, int):
        new_octal = parse_permissions(new_octal)
    return compare_permissions(old_octal, new_octal)

def extract_file_details(tar_path):
    """Extract file names and permissions from a tar archive."""
    file_details = {} # filename -> (permissions, size)
    with tarfile.open(tar_path, 'r') as tar:
        for member in tar.getmembers():
            file_details[member.name] = (member.mode, member.size)

            # If it's a symlink, also add details about the target
            if member.issym():
                # First, check does the symlink target exist within the archive

                # Resolve relative targets to absolute
                if not member.linkname.startswith("/"):
                    # Combine with the directory of the link and normalize
                    target = os.path.normpath(os.path.dirname(member.name) + "/" + member.linkname)
                else:
                    target = member.linkname

                while "//" in target:
                    target = target.replace("//", "/")

                if not target.startswith("/") and not target.startswith("./"):
                    target = "./" + target

                if not target.startswith("."):
                    target = "." + target

                try:
                    tar.getmember(target)
                    exists = True
                except KeyError:
                    exists = False
                file_details[member.name + " -> " + target + (" (missing)" if not exists else "")] = (member.mode, member.size)

    return file_details

def analyze_paths(f1k, f2k, f1, f2):
    """
    We have keys for sets in f1k/f2k. We have the details in f1/f2
    Find basenames that match between the two where the sizes are the same
    """

    matches = [] # (f1 path, f2 path)
    f1k_basenames = {os.path.basename(f): f for f in f1k}
    f2k_basenames = {os.path.basename(f): f for f in f2k}

    # For union of basenames, check if the sizes+perms are the same
    for basename in set(f1k_basenames.keys()) | set(f2k_basenames.keys()):
        if basename in f1k_basenames and basename in f2k_basenames:
            if f1[f1k_basenames[basename]] == f2[f2k_basenames[basename]]:
                matches.append((f1k_basenames[basename], f2k_basenames[basename]))

    return matches




def diff_tar_archives(tar1_path, tar2_path):
    """Compare two tar archives and return differences."""
    try:
        tar1_files = extract_file_details(tar1_path)
        tar2_files = extract_file_details(tar2_path)
    except EOFError:
        return [], [], None

    unique_to_tar1 = set(tar1_files.keys()) - set(tar2_files.keys())
    unique_to_tar2 = set(tar2_files.keys()) - set(tar1_files.keys())

    same_files_different_paths = analyze_paths(unique_to_tar1, unique_to_tar2, tar1_files, tar2_files)

    for f1, f2 in same_files_different_paths:
        unique_to_tar1.remove(f1)
        unique_to_tar2.remove(f2)


    #perm_diff = {f: permission_difference(tar1_files[f], tar2_files[f])
    #             for f in tar1_files
    #             if f in tar2_files and tar1_files[f] != tar2_files[f]}
    perms = {f: (tar1_files[f][0], tar2_files[f][0])
                 for f in tar1_files
                 if f in tar2_files and tar1_files[f][0] != tar2_files[f][0]}

    return unique_to_tar1, unique_to_tar2, perms, same_files_different_paths

def main(tar1_path, tar2_path, compare_perms=True, show_examples=True):
    try:
        unique_to_tar1, unique_to_tar2, perms, moved_files = diff_tar_archives(tar1_path, tar2_path)

        if len(unique_to_tar1):
            print(f"{len(unique_to_tar1)} files unique to {tar1_path}:")
            if show_examples:
                for f in unique_to_tar1:
                    print("\t", f)

        if len(unique_to_tar2):
            print(f"{len(unique_to_tar2)} files unique to {tar2_path}:")
            if show_examples:
                for f in unique_to_tar2:
                    print("\t", f)

        if len(moved_files):
            print(f"{len(moved_files)} files with the same content but different paths:")
            if show_examples:
                for f1, f2 in moved_files:
                    print(f"\t{f1} ==> {f2}")

        if not compare_perms:
            return

        print(f"{len(perms)} files with different permissions from {tar1_path} to {tar2_path}:")
        diffs = {} # diff -> count
        diff_files = {} # diff -> [files]
        for f, (p1, p2) in perms.items():
            diffs[(p1, p2)] = diffs.get((p1, p2), 0) + 1
            diff_files[(p1, p2)] = diff_files.get((p1, p2), []) + [f]

        # Sort by count
        for (p1, p2), count in sorted(diffs.items(), key=lambda x: x[1], reverse=True):
            #print(f"\t{count:> 5} files have diff {combine_perms(permission_difference(p1, p2))} ({permission_to_string(p1)} -> {permission_to_string(p2)})")
            assert(p1 != p2)
            print(f"\t{count:> 5} files {permission_to_string(p1)} -> {permission_to_string(p2)}")
            # Print 5 files with the difference
            for f in diff_files[(p1, p2)][:5]:
                print("\t\t Example:", f)

    except Exception as e:
        print(f"Error processing archives: {e}")
        raise

def test():
    a = parse_permissions(0o755) # rwxr-xr-x
    b = parse_permissions(0o644) # rw-r--r--
    print(a)
    print(b)
    delta = compare_permissions(a, b)
    print(delta)
    final = combine_perms(delta)
    print(final)
    assert(final == "u-x,g-x,o-x")

if __name__ == '__main__':
    from sys import argv

    if len(argv) == 2 and ".rootfs." in argv[1]:
        # Expert usage: just pass in the rootfs path and we'll set arg2 to the same
        # but with .rootfs. -> .binwalk.0.
        argv.append(argv[1].replace(".rootfs.", ".binwalk.0."))

    if len(argv) < 3:
        raise ValueError("Usage: python diff_archives.py [--noperms] [--noexamples] <tar1_path> <tar2_path>")

    perms=True
    if '--noperms' in argv:
        perms=False

    examples=True
    if '--noexamples' in argv:
        examples=False

    tar1_path, tar2_path = argv[-2], argv[-1]

    main(tar1_path, tar2_path, compare_perms=perms, show_examples=examples)
