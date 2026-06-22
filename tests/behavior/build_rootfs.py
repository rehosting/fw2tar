#!/usr/bin/env python3
"""Build the canonical synthetic rootfs used by the behavior characterization tests.

This produces a directory tree exercising the filesystem properties the rehosting
unblob fork promises to preserve (see tests/BEHAVIOR.md), and writes an
`expected.json` describing the intended output so `check_behavior.py` can assert
against it.

The tree is deliberately built to look like a real Linux rootfs so that fw2tar's
`find_linux_filesystems` heuristic selects it:
  - every KEY_DIR (bin etc lib usr var) plus the CRITICAL_FILES (bin/sh, etc/passwd)
  - well over the 10-executable minimum

Run unprivileged. Modes (including suid/sgid/sticky) are set with chmod; ownership
is whatever the building user has (the characterization focuses on MODES, which is
where issue #52 bites).
"""
import json
import os
import stat
import sys
from pathlib import Path

# (relative path, octal mode). Directories end with "/".
DIRS = [
    ("bin/", 0o755),
    ("sbin/", 0o755),
    ("etc/", 0o755),
    ("etc/init.d/", 0o755),
    ("lib/", 0o755),
    ("usr/", 0o755),
    ("usr/bin/", 0o755),
    ("var/", 0o700),          # intentionally restrictive dir — must survive verbatim
    ("www/", 0o755),          # the issue #52 case: docroot must stay world-traversable
    ("opt/", 0o755),
    ("opt/sgid/", 0o2750),    # setgid dir
    ("opt/sticky/", 0o1777),  # sticky dir
]

FILES = [
    ("bin/sh", 0o755, b"#!/bin/sh\n"),
    ("bin/busybox", 0o4755, b"\x7fELF busybox\n"),  # setuid binary
    ("sbin/init", 0o755, b"\x7fELF init\n"),
    ("etc/passwd", 0o644, b"root:x:0:0:root:/root:/bin/sh\n"),
    ("etc/shadow", 0o600, b"root:*:0:0:99999:7:::\n"),  # secret file — must stay 0600
    ("etc/init.d/rcS", 0o755, b"#!/bin/sh\n"),
    ("lib/libc.so.0", 0o644, b"\x7fELF libc\n"),
    ("www/index.html", 0o644, b"<html></html>\n"),
    ("opt/sgid/tool", 0o2755, b"\x7fELF tool\n"),  # setgid binary
]

# Plenty of executables so find_linux_filesystems selects this as a rootfs.
for _i in range(12):
    FILES.append((f"usr/bin/prog{_i}", 0o755, b"\x7fELF\n"))

# (linkpath, target). Both relative and absolute — the fork allows absolute symlinks.
SYMLINKS = [
    ("rel_link", "bin/sh"),
    ("abs_link", "/bin/sh"),
    ("usr/bin/sh", "../../bin/sh"),
]


def build(root: Path) -> dict:
    expected: dict = {}

    # Root dir: fw2tar forces the archive root to 0755 (see archive.rs).
    expected[""] = {"type": "directory", "mode": oct(0o755)}

    for rel, mode in DIRS:
        p = root / rel
        p.mkdir(parents=True, exist_ok=True)
        os.chmod(p, mode)
        expected[rel.rstrip("/")] = {"type": "directory", "mode": oct(mode)}

    for rel, mode, data in FILES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        os.chmod(p, mode)
        expected[rel] = {"type": "file", "mode": oct(mode), "size": len(data)}

    for link, target in SYMLINKS:
        p = root / link
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink() or p.exists():
            p.unlink()
        os.symlink(target, p)
        # Symlink mode is not meaningful; we assert type + target only.
        expected[link] = {"type": "symlink", "linkname": target}

    # Re-apply the restrictive/special dir modes last: creating children under a
    # dir does not change its mode, but mkdir(parents=True) above may have created
    # ancestors with default modes — make the intent explicit.
    for rel, mode in DIRS:
        os.chmod(root / rel, mode)

    return expected


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <rootfs_dir> <expected.json>", file=sys.stderr)
        sys.exit(2)

    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    expected = build(root)

    Path(sys.argv[2]).write_text(json.dumps(expected, indent=2, sort_keys=True))
    print(f"built rootfs at {root} ({len(expected)} entries), expectations -> {sys.argv[2]}")


if __name__ == "__main__":
    main()
