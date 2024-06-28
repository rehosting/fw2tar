import unittest
import tempfile
import os
import shutil
from typing import List, Optional, Dict
from subprocess import check_output

from unifyfs import FilesystemUnifier

class TestFilesystemUnifier(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.unifier = FilesystemUnifier()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def create_test_filesystem(self, name: str, files: List[str], references: Optional[List[str]] = None) -> str:
        if references is None:
            references = []
        
        fs_dir = os.path.join(self.test_dir, name)
        os.makedirs(fs_dir)
        
        for file in files:
            file_path = os.path.join(fs_dir, file.lstrip('/'))
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            if not file.endswith('/'):
                with open(file_path, 'w') as f:
                    f.write(f"Placeholder file")
        
        # Add references to a special file
        if references:
            with open(os.path.join(fs_dir, 'test_references'), 'w') as f:
                for ref in references:
                    f.write(f"{ref}\n")
        
        # Directly use tar to ensure files start with ./ and are relative to test_dir/name
        check_output(["tar", "czf", f"{fs_dir}.tar.gz", "-C", f"{self.test_dir}/{name}", "."])
        
        # Delete fs_dir
        shutil.rmtree(fs_dir)
        
        # Return name of tarball
        return f"{name}.tar.gz"

    def test_basic_unification(self):
        # Create test filesystems
        root = self.create_test_filesystem("root_fs", [
            "etc/", "bin/", "lib/", "usr/", "var/",
            "etc/passwd", "etc/fstab", "bin/ls", "bin/bash"
        ], ["/mnt/data1.txt", "/usr/local/bin/custom_app"])
        
        mnt = self.create_test_filesystem("data_fs", [
            "data1.txt", "data2.txt", "subdir/data3.txt"
        ])
        
        usr = self.create_test_filesystem("app_fs", [
            "bin/", "local/bin/", "local/bin/custom_app"
        ])

        # Load filesystems
        self.unifier.load_filesystems(self.test_dir)

        # Unify
        mount_points = self.unifier.unify()

        # Check results
        self.assertEqual(mount_points["./"], root)
        self.assertIn("./mnt", mount_points)
        self.assertEqual(mount_points["./mnt"], mnt)
        self.assertIn("./usr", mount_points)
        self.assertEqual(mount_points["./usr"], usr)

        # Verify the unified filesystem structure
        unified_fs = self.unifier._realize_fs()
        self.assertIn("./etc/passwd", unified_fs)
        self.assertIn("./mnt/data1.txt", unified_fs)
        self.assertIn("./usr/local/bin/custom_app", unified_fs)

    def test_no_root_filesystem(self):
        data_fs = self.create_test_filesystem("data_fs", [
            "data1.txt", "data2.txt"
        ])

        self.unifier.load_filesystems(self.test_dir)
        with self.assertRaises(ValueError):
            self.unifier.unify()

    def test_multiple_potential_root_filesystems(self):
        # This test is subtle as there are two potential rootfs candidates
        # root has a reference to /mnt/etc/hosts which will only work if
        # mnt ends up at /mnt.
        root = self.create_test_filesystem("fs1", [
            "etc/", "bin/", "lib/", "usr/", "var/",
            "bin/busybox", "bin/bash",
            "etc/passwd", "etc/not_hosts",
        ], ["/mnt/etc/hosts"])
        mnt = self.create_test_filesystem("fs2", [
            "etc/", "bin/", "lib/", "usr/", "var/",
            "bin/busybox", "bin/bash",
            "etc/passwd", "etc/hosts",
        ], ["/etc/passwd"])

        self.unifier.load_filesystems(self.test_dir)
        mount_points = self.unifier.unify()

        self.assertIn("./", mount_points) # Should mount root at ./
        self.assertEqual(mount_points["./"], root)
        self.assertIn("./mnt", mount_points) # should mount mnt at ./mnt
        self.assertEqual(mount_points["./mnt"], mnt)
        self.assertEqual(len(mount_points), 2)  # Root and the other fs

    def test_multiple_potential_root_filesystems_flip(self):
        # This test is subtle as there are two potential rootfs candidates
        # root has a reference to /mnt/etc/hosts which will only work if
        # mnt ends up at /mnt.
        root = self.create_test_filesystem("fs2", [
            "etc/", "bin/", "lib/", "usr/", "var/",
            "bin/busybox", "bin/bash",
            "etc/passwd", "etc/not_hosts",
        ], ["/mnt/etc/hosts"])
        mnt = self.create_test_filesystem("fs1", [
            "etc/", "bin/", "lib/", "usr/", "var/",
            "bin/busybox", "bin/bash",
            "etc/passwd", "etc/hosts",
        ], ["/etc/passwd"])

        self.unifier.load_filesystems(self.test_dir)
        mount_points = self.unifier.unify()

        self.assertIn("./", mount_points) # Should mount root at ./
        self.assertEqual(mount_points["./"], root)
        self.assertIn("./mnt", mount_points) # should mount mnt at ./mnt
        self.assertEqual(mount_points["./mnt"], mnt)
        self.assertEqual(len(mount_points), 2)  # Root and the other fs
    

if __name__ == '__main__':
    unittest.main()