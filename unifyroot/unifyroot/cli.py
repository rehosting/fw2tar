from typing import Optional
from .common import FilesystemRepository, FilesystemLoader
from .filesystemunifier import FilesystemUnifier

def unify_filesystems(input_path: str, output_path: Optional[str] = None):
    '''
    Given a directory (or a path to a .tar.gz within such a directory),
    examine all the archives and find an optimal way to unify them into a single filesystem.
    Create the unified filesystem at output_path.
    '''
    repository = FilesystemRepository()
    loader = FilesystemLoader(repository)
    loader.load_filesystems(input_path)
    unifier = FilesystemUnifier(repository)
    mount_points = unifier.unify()

    print(f"Best mount points: {mount_points}")

    if output_path is None:
        output_path = input_path + "unified.tar.gz"
    unifier.create_archive(loader.load_path, mount_points, output_path)

def main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: unifyroot <input_path> [output_path]")
        sys.exit(1)
    unify_filesystems(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)

if __name__ == "__main__":
    main()