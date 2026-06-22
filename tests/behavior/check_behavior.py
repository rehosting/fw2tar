#!/usr/bin/env python3
"""Assert a fw2tar output archive against the canonical expectations.

Two modes:
  * golden (default): every expected entry must be present with the right type,
    mode (incl. suid/sgid/sticky), and symlink target. Any mismatch fails.
  * known-bug (--expect-bug): the fixture currently exercises a real bug, so
    mismatches are EXPECTED (reported as XFAIL, exit 0). If everything matches
    instead, that's an XPASS (exit 3) — the bug got fixed and the fixture should
    be promoted to golden.

Extra entries in the archive (e.g. ext4 `lost+found`) are ignored; we only assert
the entries we deliberately created.
"""
import argparse
import json
import sys
from pathlib import Path

# Reuse the tar reader from the sibling tests/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tar_to_json import extract_tar_to_json  # noqa: E402

PERM_BITS = 0o7777


def norm(path: str) -> str:
    """Normalize a tar member name to a comparison key: no './' prefix, no trailing '/'."""
    if path.startswith("./"):
        path = path[2:]
    elif path == ".":
        path = ""
    return path.rstrip("/")


def load_actual(tar_path: str) -> dict:
    raw = extract_tar_to_json(tar_path)
    return {norm(k): v for k, v in raw.items()}


def compare(expected: dict, actual: dict, types_only: bool = False) -> list:
    """Return a list of human-readable mismatch strings (empty == perfect match).

    With types_only=True (for filesystems that cannot carry unix metadata, e.g.
    FAT), only presence and file/dir type are asserted: modes are ignored and
    symlink entries are skipped entirely (the filesystem can't represent them).
    """
    problems = []
    for key, exp in sorted(expected.items()):
        disp = key or "<root>"
        if types_only and exp["type"] == "symlink":
            continue
        act = actual.get(key)
        if act is None:
            problems.append(f"{disp}: MISSING from archive")
            continue
        if act["type"] != exp["type"]:
            problems.append(f"{disp}: type {act['type']} != expected {exp['type']}")
            continue
        if types_only:
            continue
        if exp["type"] == "symlink":
            if act.get("linkname") != exp["linkname"]:
                problems.append(
                    f"{disp}: symlink -> {act.get('linkname')!r} != expected {exp['linkname']!r}"
                )
        else:
            exp_mode = int(exp["mode"], 8) & PERM_BITS
            act_mode = int(act["mode"], 8) & PERM_BITS
            if exp_mode != act_mode:
                problems.append(
                    f"{disp}: mode {act_mode:#o} != expected {exp_mode:#o}"
                )
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tar", required=True, help="fw2tar output .rootfs.tar.gz")
    ap.add_argument("--expected", required=True, help="expected.json")
    ap.add_argument("--name", default="fixture", help="fixture label for output")
    ap.add_argument("--expect-bug", action="store_true",
                    help="treat mismatches as an expected (known-bug) XFAIL")
    ap.add_argument("--types-only", action="store_true",
                    help="assert only presence + file/dir type (for FAT etc. that "
                         "cannot carry unix modes/symlinks)")
    ap.add_argument("--report", action="store_true",
                    help="machine-readable: print the mismatch count to stdout "
                         "(0 = perfect), detail to stderr, and always exit 0")
    args = ap.parse_args()

    expected = json.loads(Path(args.expected).read_text())
    actual = load_actual(args.tar)

    if args.report:
        if not actual:
            print("ERR")
            return 0
        problems = compare(expected, actual, types_only=args.types_only)
        print(len(problems))
        for p in problems:
            print(f"    - {p}", file=sys.stderr)
        return 0

    if not actual:
        print(f"[{args.name}] ERROR: archive {args.tar} is empty or unreadable")
        return 1

    problems = compare(expected, actual, types_only=args.types_only)

    if args.expect_bug:
        if problems:
            print(f"[{args.name}] XFAIL (known bug) — {len(problems)} expected mismatch(es):")
            for p in problems[:15]:
                print(f"    - {p}")
            if len(problems) > 15:
                print(f"    ... and {len(problems) - 15} more")
            return 0
        print(f"[{args.name}] XPASS — known bug appears FIXED. "
              f"Promote this fixture to golden (drop --expect-bug).")
        return 3

    if problems:
        print(f"[{args.name}] FAIL — {len(problems)} mismatch(es):")
        for p in problems:
            print(f"    - {p}")
        return 1

    print(f"[{args.name}] PASS — {len(expected)} entries match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
