import tarfile
import os

def octal_permissions(mode):
    """Convert file mode to octal permissions string."""
    rv = oct(mode)[-3:]

    if len(oct(mode)) == 4:
        # We have 0o12 or something, need to left pad with 0 before the o
        rv = "0" + rv.split("o")[-1]

    elif len(oct(mode)) == 3:
        # We have 0o2 or something, need to left pad with 0 before the o
        rv = "00" + rv.split("o")[-1]

    assert(len(rv) == 3), f"Invalid octal permissions: {rv}: {oct(mode)}"
    assert 'o' not in rv, f"Invalid octal permissions: {rv}"
    return rv



def check_tar_permissions(tar_path):
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            # Skip directories
            if member.isdir():
                continue
            # Extract expected permissions from the filename
            expected_perms = os.path.basename(member.name).split('0o')[-1]
            # Convert file mode to octal permissions
            actual_perms = octal_permissions(member.mode)

            # Compare expected and actual permissions
            if expected_perms != actual_perms:
                print(f"Mismatch in {member.name}: Expected 0o{expected_perms}, Found 0o{actual_perms}")

if __name__ == "__main__":
    from sys import argv
    tar_path = argv[1]
    check_tar_permissions(tar_path)