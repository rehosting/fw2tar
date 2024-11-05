import glob
import os
import re
import tarfile

from io import BytesIO
from elftools.elf.elffile import ELFFile
from typing import Dict, Set, Optional

class FilesystemInfo:
    """
    Represents information about a filesystem.

    Attributes:
        name (str): The name of the filesystem.
        paths (Set[str]): Set of paths in the filesystem.
        references (Set[str]): Set of references found in the filesystem.
        size (int): Total size of the filesystem in bytes.
    """

    def __init__(self, name: str):
        self.name: str = name
        self.paths: Set[str] = set()
        self.references: Set[str] = set()
        self.size: int = 0
        self.links: Dict[str, str] = {}

    def add_path(self, path: str) -> None:
        """Add a path to the filesystem."""
        self.paths.add(path)

    def add_link(self, path: str, link: str) -> None:
        """Add a link to the filesystem."""
        self.links[path] = link


    def add_reference(self, reference: str) -> None:
        """
        Add a reference to the filesystem.

        Args:
            reference (str): The reference to add. Must not contain spaces as a sainity check
            (Maybe drop that assertion?)
        """
        assert " " not in reference, "References cannot contain spaces"
        self.references.add(reference)

    def set_size(self, size: int) -> None:
        """Set the total size of the filesystem."""
        self.size = size

class FilesystemRepository:
    """
    Manages a collection of FilesystemInfo objects.

    Attributes:
        filesystems (Dict[str, FilesystemInfo]): A dictionary mapping filesystem names to FilesystemInfo objects.
    """

    def __init__(self):
        self.filesystems: Dict[str, FilesystemInfo] = {}

    def add_filesystem(self, name: str) -> None:
        """
        Add a new filesystem to the repository if it doesn't already exist.

        Args:
            name (str): The name of the filesystem to add.
        """
        if name not in self.filesystems:
            self.filesystems[name] = FilesystemInfo(name)

    def get_filesystem(self, name: str) -> Optional[FilesystemInfo]:
        """
        Retrieve a filesystem by name.

        Args:
            name (str): The name of the filesystem to retrieve.

        Returns:
            Optional[FilesystemInfo]: The FilesystemInfo object if found, None otherwise.
        """
        return self.filesystems.get(name)

    def get_all_filesystems(self) -> Dict[str, FilesystemInfo]:
        """
        Get all filesystems in the repository.

        Returns:
            Dict[str, FilesystemInfo]: A dictionary of all filesystems.
        """
        return self.filesystems

    def add_path_to_filesystem(self, name: str, path: str) -> None:
        """
        Add a path to a specific filesystem.

        Args:
            name (str): The name of the filesystem.
            path (str): The path to add.
        """
        if name in self.filesystems:
            self.filesystems[name].add_path(path)

    def add_link_to_filesystem(self, name: str, path: str, link: str) -> None:
        """
        Add a link to a specific filesystem.

        Args:
            name (str): The name of the filesystem.
            path (str): The path to add.
            link (str): The link to add.
        """
        if name in self.filesystems:
            self.filesystems[name].add_link(path, link)

    def add_reference_to_filesystem(self, name: str, reference: str) -> None:
        """
        Add a reference to a specific filesystem. But only if it's valid

        Args:
            name (str): The name of the filesystem.
            reference (str): The reference to add.
        """
        if name in self.filesystems:
            self.filesystems[name].add_reference(reference)

    def set_filesystem_size(self, name: str, size: int) -> None:
        """
        Set the size of a specific filesystem.

        Args:
            name (str): The name of the filesystem.
            size (int): The size to set.
        """
        if name in self.filesystems:
            self.filesystems[name].set_size(size)

class FilesystemLoader:
    """
    Loads filesystem information from tar.gz files into a FilesystemRepository.

    Attributes:
        repository (FilesystemRepository): The repository to store loaded filesystem information.
        load_path (Optional[str]): The path from which filesystems are being loaded.
    """

    def __init__(self, repository: FilesystemRepository):
        self.repository = repository
        self.load_path: Optional[str] = None

    def load_filesystems(self, input_path: str) -> None:
        """
        Load filesystems from a given input path.

        Args:
            input_path (str): Path to a directory containing tar.gz files or a single tar.gz file.

        Raises:
            ValueError: If the input path is neither a directory nor a tar.gz file.
        """
        if input_path.endswith(".tar.gz"):
            glob_target = f"{input_path[:-7]}*.tar.gz"
            self.load_path = os.path.dirname(input_path)
        elif os.path.isdir(input_path):
            glob_target = f"{input_path}/*.tar.gz"
            self.load_path = input_path
        else:
            raise ValueError(f"Input path must be a directory or a .tar.gz file. {input_path} is neither")

        for file in glob.glob(glob_target):
            self._process_tar_file(file)

    def _process_tar_file(self, file_path: str) -> None:
        """
        Process a single tar.gz file and extract filesystem information.

        Args:
            file_path (str): Path to the tar.gz file to process.
        """
        fs_name = os.path.basename(file_path)
        self.repository.add_filesystem(fs_name)

        with tarfile.open(file_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name == ".":
                    continue
                if member.islnk() or member.issym():
                    # Add as both link and path
                    self.repository.add_link_to_filesystem(fs_name, member.name, member.linkname)
                    self.repository.add_path_to_filesystem(fs_name, member.name)
                elif member.isfile():
                    self.repository.add_path_to_filesystem(fs_name, member.name)
                    self._extract_references(fs_name, tar, member)
                elif member.isdir():
                    self.repository.add_path_to_filesystem(fs_name, member.name)

            self.repository.set_filesystem_size(fs_name, sum(member.size for member in tar.getmembers()))

    def _extract_references(self, fs_name: str, tar: tarfile.TarFile, member: tarfile.TarInfo) -> None:
        """
        Extract references from a file in the tar archive.

        Args:
            fs_name (str): Name of the filesystem.
            tar (tarfile.TarFile): The tar archive being processed.
            member (tarfile.TarInfo): The specific file in the tar archive to process.
        """
        file_content = tar.extractfile(member).read()
        path_regex = re.compile(r'/[^/\0\n<>"\'! :\?]{3,255}(?:/[^/\0\n<>()%"\'! ;:\?]+)*')

        # If it's an elf try parsing and finding libraries it references
        elf_magic = b"\x7fELF"
        elf_references = None
        if file_content.startswith(elf_magic):
            try:
                elf_references = self._parse_elf_references(file_content)
            except Exception as e:
                # Never seen an exception yet but maybe we'll get a malformed elf one day?
                print(e)
                pass

        if elf_references is not None:
            for reference in elf_references:
                if self._is_valid_reference(reference) and path_regex.match(reference):
                    self.repository.add_reference_to_filesystem(fs_name, reference)
        else:

            # ignore HTML like files as a source for information
            if member.name.endswith((".html", ".htm", ".css", ".js")):
                return

            try:
                file_content = file_content.decode('utf-8')
            except UnicodeDecodeError:
                # Non-UTF-8 file, skip (?) should we try parsing other ways
                # Goal here is to find config files and things like that
                return

            # Fallback to regex for finding references
            for match in re.findall(path_regex, file_content):
                if self._is_valid_reference(match):
                    self.repository.add_reference_to_filesystem(fs_name, match)

    def _parse_elf_references(self, elf_content: bytes) -> Set[str]:
        """
        Extract references from an ELF file in the tar archive.
        """

        lib_paths = ["/lib", "/usr/lib"]

        with ELFFile(BytesIO(memoryview(elf_content))) as elf:
            references = set()

            dynamic = elf.get_section_by_name('.dynamic')

            # Find RPATH - influences library search path
            rpath = None
            if dynamic:
                for tag in dynamic.iter_tags():
                    if tag.entry.d_tag == 'DT_RPATH':
                        rpath = tag.rpath
                        lib_paths.append(rpath)

            # Find interpreter path
            interp = elf.get_section_by_name('.interp')
            if interp:
                interp_data = interp.data().strip(b'\x00')
                references.add(interp_data.decode('utf-8', errors='ignore'))

            # Parse the dynamic section for DT_NEEDED (shared libraries)
            if dynamic:
                for tag in dynamic.iter_tags():
                    if tag.entry.d_tag == 'DT_NEEDED':
                        if not tag.needed:
                            continue
                        needed = tag.needed
                        if needed.startswith('/'):
                            references.add(needed)
                        else:
                            # XXX: We're adding multiple paths, but only one needs to work
                            for lib in lib_paths:
                                references.add(os.path.join(lib, needed))


            # XXX do we want this?
            strtab = elf.get_section_by_name('.strtab')
            if strtab:
                for match in re.findall(rb'^/([a-zA-Z0-9_\-./]+)*$', strtab.data()):
                    references.add(match.decode())
        return references



    @staticmethod
    def _is_valid_reference(path: str) -> bool:
        """
        Check if a reference path is valid.

        Args:
            path (str): The path to check.

        Returns:
            bool: True if the path is a valid reference, False otherwise.
        """
        if not (3 < len(path) < 255):
            # Too short or too long
            return False

        if path.replace("/", "").isnumeric():
            # Purely numeric? Probably don't want it, it's a date like 9/1992
            return False

        if path.endswith(".c"):
            # Don't want source paths
            return False

        if len(path.split("/")) < 3:
            # Too short, probably not a reference
            return False

        # Is it a website?
        if path.startswith("/www.") or ".com/" in path:
            return False

        # Does it start with an IP address
        potential_ip = path.split("/")[1]
        if len(potential_ip.split(".")) == 4 and all(part.isnumeric() for part in potential_ip.split(".")):
            return False

        invalid_chars = set(" \t\n^$%*{}`\+,=\\")
        if any(invalid_chars & set(path)):
            # Invalid characters
            return False

        return True
