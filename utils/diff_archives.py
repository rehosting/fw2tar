import tarfile
import os

def parse_permissions(octal_permission):
    """Convert octal permission to a list of binary permissions, including special bits."""
    binary_permission = bin(octal_permission)[2:].zfill(12)
    return [binary_permission[:3]] + [binary_permission[i:i+3] for i in range(3, 12, 3)]

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
    file_details = {}
    with tarfile.open(tar_path, 'r') as tar:
        for member in tar.getmembers():
            file_details[member.name] = member.mode

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
                file_details[member.name + " -> " + target + (" (missing)" if not exists else "")] = member.mode

    return file_details

def diff_tar_archives(tar1_path, tar2_path):
    """Compare two tar archives and return differences."""
    try:
        tar1_files = extract_file_details(tar1_path)
        tar2_files = extract_file_details(tar2_path)
    except EOFError:
        return [], [], None

    unique_to_tar1 = set(tar1_files.keys()) - set(tar2_files.keys())
    unique_to_tar2 = set(tar2_files.keys()) - set(tar1_files.keys())
    #perm_diff = {f: permission_difference(tar1_files[f], tar2_files[f])
    #             for f in tar1_files
    #             if f in tar2_files and tar1_files[f] != tar2_files[f]}
    perms = {f: (tar1_files[f], tar2_files[f])
                 for f in tar1_files
                 if f in tar2_files and tar1_files[f] != tar2_files[f]}

    return unique_to_tar1, unique_to_tar2, perms

def main(tar1_path, tar2_path, compare_perms=True, show_examples=True):
    try:
        unique_to_tar1, unique_to_tar2, perms = diff_tar_archives(tar1_path, tar2_path)

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

        if not compare_perms:
            return

        print(f"{len(perms)} files with different permissions from {tar1_path} to {tar2_path}:")
        diffs = {} # diff -> count
        diff_files = {} # diff -> [files]
        for f, (p1, p2) in perms.items():
            #print(f"\t{f}: {diff}")
            diffs[(p1, p2)] = diffs.get((p1, p2), 0) + 1
            diff_files[(p1, p2)] = diff_files.get((p1, p2), []) + [f]

        # Sort by count
        for (p1, p2), count in sorted(diffs.items(), key=lambda x: x[1], reverse=True):
            #print(f"\t{count:> 5} files have diff {combine_perms(permission_difference(p1, p2))} ({permission_to_string(p1)} -> {permission_to_string(p2)})")
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

    if len(argv) == 2 and ".binwalk." in argv[1]:
        # Expert usage: just pass in the binwalk path and we'll set tar2 to the same
        # but with .binwalk. -> .unblob.
        argv.append(argv[1].replace(".binwalk.", ".unblob."))

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
