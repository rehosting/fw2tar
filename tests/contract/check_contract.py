#!/usr/bin/env python3
"""Verify a fw2tar primary archive's manifest contract.

Checks, for `<base>.rootfs.tar.gz`:
  - the `<archive>.manifest.json` sidecar exists, parses, and carries the
    required fields (version, input_hash, file, fw2tar_command, extractor)
  - the manifest embedded in the archive's gzip trailer (the frame written by
    src/archive.rs write_manifest_trailer) parses and is IDENTICAL to the
    sidecar — consumers may read either, so the two must never diverge
  - with --secondaries N: the sidecar advertises exactly N secondary
    filesystems (indices 1..N), each resolving (relative to the primary) to an
    existing archive with its own consistent sidecar + trailer
  - without --secondaries: no secondary_filesystems are advertised
  - with --input: input_hash matches the sha1 of the original firmware

Stdlib only; runs on the host.
"""
import argparse
import gzip
import hashlib
import json
import struct
import sys
from pathlib import Path

MANIFEST_MAGIC = b"made with fw2tar"
REQUIRED_FIELDS = ("version", "input_hash", "file", "fw2tar_command", "extractor")

failures = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"  ok   {msg}")


def read_trailer_manifest(archive: Path) -> dict | None:
    """Parse the manifest from the decompressed tail (inverse of
    archive.rs write_manifest_trailer; python gzip handles the multi-member
    concatenation transparently, matching parse_manifest_trailer)."""
    data = gzip.decompress(archive.read_bytes())
    if len(data) < len(MANIFEST_MAGIC) + 6 or not data.endswith(MANIFEST_MAGIC):
        return None
    rest = data[: -len(MANIFEST_MAGIC)]
    (json_len,) = struct.unpack("<I", rest[-6:-2])
    (frame_version,) = struct.unpack("<H", rest[-2:])
    if frame_version != 1:
        return None
    json_end = len(rest) - 6
    if json_len > json_end:
        return None
    return json.loads(rest[json_end - json_len : json_end])


def check_archive(archive: Path, extractors: list[str] | None) -> dict | None:
    """Common per-archive checks; returns the sidecar manifest (or None)."""
    sidecar_path = archive.parent / f"{archive.name}.manifest.json"
    if not sidecar_path.is_file():
        fail(f"missing manifest sidecar: {sidecar_path.name}")
        return None
    try:
        sidecar = json.loads(sidecar_path.read_text())
    except json.JSONDecodeError as e:
        fail(f"sidecar {sidecar_path.name} is not valid JSON: {e}")
        return None
    ok(f"sidecar parses: {sidecar_path.name}")

    for field in REQUIRED_FIELDS:
        if field not in sidecar:
            fail(f"{sidecar_path.name}: missing required field {field!r}")
    if extractors and sidecar.get("extractor") not in extractors:
        fail(
            f"{sidecar_path.name}: extractor {sidecar.get('extractor')!r} "
            f"not in requested set {extractors}"
        )

    trailer = read_trailer_manifest(archive)
    if trailer is None:
        fail(f"{archive.name}: no parseable manifest trailer in gzip stream")
    elif trailer != sidecar:
        fail(f"{archive.name}: embedded trailer manifest differs from sidecar")
    else:
        ok(f"embedded trailer matches sidecar: {archive.name}")
    return sidecar


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("primary", type=Path, help="path to <base>.rootfs.tar.gz")
    ap.add_argument("--extractors", help="comma-separated set the run requested")
    ap.add_argument(
        "--secondaries",
        default="0",
        help="expected number of secondary filesystems (default 0), or 'auto' "
        "to accept any count but still validate that every advertised "
        "secondary resolves and is internally consistent",
    )
    ap.add_argument("--input", type=Path, help="original firmware, to check input_hash")
    args = ap.parse_args()

    extractors = args.extractors.split(",") if args.extractors else None
    if not args.primary.is_file():
        fail(f"primary archive missing: {args.primary}")
        return 1

    sidecar = check_archive(args.primary, extractors)
    if sidecar is None:
        return 1

    if args.input:
        want = hashlib.sha1(args.input.read_bytes()).hexdigest()
        if sidecar.get("input_hash") != want:
            fail(f"input_hash {sidecar.get('input_hash')} != sha1(input) {want}")
        else:
            ok("input_hash matches sha1 of firmware")

    secs = sidecar.get("secondary_filesystems", [])
    if args.secondaries == "0":
        if secs:
            fail(f"unexpected secondary_filesystems advertised: {secs}")
        else:
            ok("no secondary filesystems advertised")
    else:
        indices = sorted(s.get("index") for s in secs)
        if args.secondaries == "auto":
            if indices != list(range(1, len(secs) + 1)):
                fail(f"secondary indices are not contiguous from 1: {indices}")
            else:
                ok(f"{len(secs)} secondary filesystem(s) advertised")
        elif indices != list(range(1, int(args.secondaries) + 1)):
            fail(
                f"expected secondary indices 1..{args.secondaries}, "
                f"manifest advertises {indices}"
            )
        for sec in secs:
            sec_path = args.primary.parent / sec.get("archive", "")
            if not sec_path.is_file():
                fail(f"advertised secondary does not resolve: {sec.get('archive')!r}")
                continue
            ok(f"secondary #{sec['index']} resolves: {sec_path.name}")
            sec_manifest = check_archive(sec_path, extractors)
            if sec_manifest and sec_manifest.get("secondary_filesystems"):
                fail(f"{sec_path.name}: a secondary must not advertise secondaries")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
