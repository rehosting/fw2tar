import tarfile
import os

def parse_permissions(octal_permission):
    """Convert octal permission to a list of binary permissions."""
    binary_permission = bin(octal_permission)[2:].zfill(9)
    return [binary_permission[i:i+3] for i in range(0, 9, 3)]


def compare_permissions(old_perm, new_perm):
    """Compare two permission sets and return the differences."""
    perms = ['r', 'w', 'x']
    types = ['u', 'g', 'o']
    changes = []

    for i in range(3):
        for j in range(3):
            if old_perm[i][j] != new_perm[i][j]:
                change = '+' if new_perm[i][j] == '1' else '-'
                changes.append(f"{types[i]}{change}{perms[j]}")

    return ','.join(changes)

def permission_to_string(perm):
    '''
    Convert a permission list to a standard rwxrwxrwx string
    '''
    perms = ['r', 'w', 'x']

    if isinstance(perm, int):
        perm = parse_permissions(perm)
    
    return ''.join([perms[j] if perm[i][j] == '1' else '-' for i in range(3) for j in range(3)])

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
                try:
                    tar.getmember("." + member.linkname)
                    exists = True
                except KeyError:
                    exists = False
                abs_dest = "/" + os.path.normpath(os.path.dirname(member.name) + "/" + member.linkname)
                file_details[member.name + " -> " + abs_dest + (" (missing)" if not exists else "")] = member.mode

    return file_details

def diff_tar_archives(tar1_path, tar2_path):
    """Compare two tar archives and return differences."""
    tar1_files = extract_file_details(tar1_path)
    tar2_files = extract_file_details(tar2_path)

    unique_to_tar1 = set(tar1_files.keys()) - set(tar2_files.keys())
    unique_to_tar2 = set(tar2_files.keys()) - set(tar1_files.keys())
    #perm_diff = {f: permission_difference(tar1_files[f], tar2_files[f]) 
    #             for f in tar1_files 
    #             if f in tar2_files and tar1_files[f] != tar2_files[f]}
    perms = {f: (tar1_files[f], tar2_files[f]) 
                 for f in tar1_files 
                 if f in tar2_files and tar1_files[f] != tar2_files[f]}

    return unique_to_tar1, unique_to_tar2, perms

def main(tar1_path, tar2_path):
    try:
        unique_to_tar1, unique_to_tar2, perms = diff_tar_archives(tar1_path, tar2_path)

        print(f"{len(unique_to_tar1)} files unique to {tar1_path}:")
        for f in unique_to_tar1:
            print("\t", f)

        print(f"{len(unique_to_tar2)} files unique to {tar2_path}:")
        for f in unique_to_tar2:
            print("\t", f)

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
    if len(argv) != 3:
        print("Usage: python script.py <tar1_path> <tar2_path>")
    else:
        tar1_path, tar2_path = argv[1], argv[2]
        main(tar1_path, tar2_path)