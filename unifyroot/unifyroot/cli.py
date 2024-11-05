from typing import Optional
from .common import FilesystemRepository, FilesystemLoader
from .filesystemunifier import FilesystemUnifier

def unify_filesystems(input_path: str, output_path: str):
    '''
    Given a directory (or a path to a .tar.gz within such a directory),
    examine all the archives and find an optimal way to unify them into a single filesystem.
    Create the unified filesystem at output_path.
    '''
    # TODO: should we integrate tmp_dir into any parts of analysis here?
    repository = FilesystemRepository()
    loader = FilesystemLoader(repository)
    loader.load_filesystems(input_path)
    unifier = FilesystemUnifier(repository)
    mount_points = unifier.unify()

    print(f"Best mount points: {mount_points}")

    if output_path is None:
        output_path = input_path + "unified.tar.gz"

    if len(mount_points) == 0:
        print("No mount points found, not creating archive.")
        return

    unifier.create_archive(loader.load_path, mount_points, output_path)

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: unifyroot <input_path> <output_path>")
        sys.exit(1)
    unify_filesystems(sys.argv[1], sys.argv[2])

if __name__ == "__main__":
    main()