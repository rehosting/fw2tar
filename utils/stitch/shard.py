"""Pile-of-shards extraction.

The `shard` step replaces fw2tar's single-rootfs assumption. It runs an
extractor (unblob preferred, binwalk fallback), walks the resulting tree,
identifies every directory that looks like a Linux filesystem fragment, and
emits each as its own .tar.gz alongside a `shards.json` manifest with
provenance.

Why this beats wiring up `_secondary_limit` in fw2tar's Rust:
  * fw2tar's "is this root-like?" heuristic (find_linux_filesystems.rs)
    discards UBIFS app partitions, squashfs module blobs, config-only
    partitions — exactly the shards we need to stitch.
  * unblob's `*_extract` directory naming encodes the on-disk fs type
    (squashfs/ubifs/jffs2/cpio/gzip/...). That metadata flows through to the
    stitcher as a hint for the LLM.
  * No Rust rebuild; iterates fast on host.

Where this runs: anywhere `unblob` (or `binwalk`) is on PATH. Inside the
fw2tar Docker container is the most reliable place — both extractors are
already installed there.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# Top-level dir names that, when present, scream "Linux rootfs."
ROOT_DIRS = frozenset([
    "bin", "sbin", "etc", "usr", "lib", "lib32", "lib64",
    "var", "opt", "root", "dev", "proc", "sys", "tmp",
    "home", "mnt", "media", "run", "boot", "srv",
])

# Specific paths that strongly indicate a rootfs.
ROOTFS_FILES = (
    "etc/passwd", "etc/inittab", "etc/fstab", "etc/init.d/rcS",
    "sbin/init", "bin/sh", "bin/busybox",
)

# Filesystem types whose default extractor in unblob/binwalk (7z) is known to
# corrupt permissions, ownership, or setuid bits. For these, we locate the
# original blob next to the *_extract dir and re-extract with the native tool.
# Maps fs_type_guess -> reextractor name (key into REEXTRACTORS below).
REEXTRACTOR_FOR_TYPE: dict[str, str] = {
    "cpio": "cpio",
}


# unblob extraction-directory suffixes -> fs type hints.
# Order matters: longer, more-specific suffixes come first.
EXTRACT_SUFFIX_TYPES: list[tuple[str, str]] = [
    ("squashfs_v4_le_extract", "squashfs"),
    ("squashfs_v4_be_extract", "squashfs"),
    ("squashfs_v3_le_extract", "squashfs"),
    ("squashfs_v3_be_extract", "squashfs"),
    ("squashfs_extract", "squashfs"),
    ("ubifs_extract", "ubifs"),
    ("ubi_extract", "ubi"),
    ("jffs2_extract", "jffs2"),
    ("cramfs_extract", "cramfs"),
    ("yaffs2_extract", "yaffs2"),
    ("yaffs_extract", "yaffs"),
    ("ramdisk_el_extract", "cpio"),
    ("ramdisk_eb_extract", "cpio"),
    ("cpio_extract", "cpio"),
    ("tar_extract", "tar"),
    ("gzip_extract", "gzip"),
    ("ext_extract", "ext"),
    ("fat_extract", "fat"),
    ("iso9660_extract", "iso9660"),
    ("romfs_extract", "romfs"),
]


@dataclass
class ShardInfo:
    name: str                 # tarball basename
    score: int                # filesystem-likeness score
    root_path: str            # path relative to the extraction root
    fs_type_guess: Optional[str]
    matched_root_dirs: list[str] = field(default_factory=list)
    matched_rootfs_files: list[str] = field(default_factory=list)
    file_count: int = 0
    reextracted_with: Optional[str] = None  # native tool used to re-extract, if any
    source_blob: Optional[str] = None       # path of the original blob (relative)


def _guess_fs_type(path: Path, extraction_root: Path) -> Optional[str]:
    """Best-effort fs type guess from unblob's directory naming. Walk up the
    ancestors until we hit a known `*_extract` suffix.
    """
    rel = path.relative_to(extraction_root)
    for part in reversed(rel.parts):
        for suffix, ty in EXTRACT_SUFFIX_TYPES:
            if part.endswith(suffix):
                return ty
    return None


def _count_files(path: Path, cap: int = 5000) -> int:
    """Cheap file count, capped to avoid pathological cost."""
    n = 0
    for _, _, filenames in os.walk(path):
        n += len(filenames)
        if n >= cap:
            return n
    return n


def score_directory(path: Path, top_children: list[str], extraction_root: Path | None = None) -> tuple[int, dict]:
    """Score how filesystem-like a directory is. Score is informational — used
    for ranking and to help the LLM tell base from overlay. Selection is
    primarily driven by unblob's `*_extract` naming (see find_shards).
    """
    top_set = set(top_children)
    matched_root_dirs = sorted(top_set & ROOT_DIRS)
    score = 5 * len(matched_root_dirs)

    matched_files: list[str] = []
    for f in ROOTFS_FILES:
        if (path / f).exists():
            score += 3
            matched_files.append(f)

    interesting_subpaths = ("etc/init.d", "usr/local", "usr/bin", "lib/modules", "etc/config")
    for sp in interesting_subpaths:
        if (path / sp).exists():
            score += 2

    if extraction_root is not None:
        try:
            rel = path.relative_to(extraction_root)
            for part in rel.parts:
                for suffix, _ty in EXTRACT_SUFFIX_TYPES:
                    if part.endswith(suffix):
                        score += 5
                        break
        except ValueError:
            pass

    file_count = _count_files(path) if score > 0 or top_children else 0

    return score, {
        "matched_root_dirs": matched_root_dirs,
        "matched_files": matched_files,
        "file_count": file_count,
    }


def _is_descendant(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return child != parent
    except ValueError:
        return False


def _has_extract_descendant(d: Path) -> bool:
    """True if any directory inside `d` is itself a `*_extract` dir."""
    for _dirpath, dirnames, _ in os.walk(d):
        for sub in dirnames:
            if sub.endswith("_extract"):
                return True
    return False


# Of the fs types in EXTRACT_SUFFIX_TYPES, these are real on-disk filesystems
# or whole-tree archives — when unblob produces a `*_<this>_extract` directory
# it IS the filesystem boundary. The remainder ("gzip", "bzip2", ...) are
# transparent compression wrappers that just unwrap to a single blob inside,
# which we still want to recurse into.
_TERMINAL_FS_TYPES = frozenset({
    "squashfs", "ubifs", "ubi", "jffs2", "cramfs", "yaffs2", "yaffs",
    "cpio", "tar", "ext", "fat", "iso9660", "romfs",
})


def _has_known_fs_type_suffix(name: str) -> bool:
    """True if the dir name carries a known on-disk-filesystem suffix
    (squashfs, ubifs, jffs2, cpio, ramdisk_el, ...) and that type is a
    full filesystem rather than a transparent compression wrapper. unblob
    applies these suffixes only when it identifies the chunk as a filesystem
    image — when present, the directory IS the filesystem regardless of
    whether unblob further recursed into individual files inside it.
    """
    for suffix, ty in EXTRACT_SUFFIX_TYPES:
        if name.endswith(suffix) and ty in _TERMINAL_FS_TYPES:
            return True
    return False


def _find_fs_root(extract_dir: Path) -> Path:
    """Descend through single-subdirectory wrappers (e.g. `squashfs-root`) to
    reach the actual filesystem root. Stops if the wrapper itself looks like a
    sub-extract or if there's branching.
    """
    current = extract_dir
    for _ in range(8):  # hard cap to avoid pathological symlink loops
        try:
            children = list(current.iterdir())
        except OSError:
            return current
        dir_children = [c for c in children if c.is_dir()]
        if (
            len(children) == 1
            and len(dir_children) == 1
            and not dir_children[0].name.endswith("_extract")
        ):
            current = dir_children[0]
            continue
        return current
    return current


def find_shards(extracted: Path, min_score: int = 3, max_depth: int = 14) -> list[tuple[Path, int, dict]]:
    """Pick filesystem-fragment leaves from an extraction tree.

    Selection rules (union):
      * Every terminal `*_extract` directory (no further `*_extract` beneath
        it) that has at least one subdirectory is a candidate. Selection is
        independent of filesystem-likeness scoring, so overlay-shape shards
        (e.g. a config-only partition) are not dropped.
      * Additionally, any directory with a strong fs-likeness score >=
        min_score is a candidate, to handle binwalk output or pre-extracted
        trees that don't use unblob's naming.

    Then return only the leaves of the candidate forest — most-specific wins.
    For each terminal extract, descend through single-child wrappers (e.g.
    `squashfs-root`) to find the real filesystem root.
    """
    # Pass 1: *_extract directories that look like a complete filesystem.
    # A directory qualifies if EITHER:
    #   (a) its name carries a known on-disk-fs suffix (ubifs_extract,
    #       squashfs_v4_le_extract, jffs2_extract, cpio_extract, ramdisk_el_extract,
    #       ...) — in that case it IS the filesystem even if unblob also
    #       recursed into a sub-blob inside it, OR
    #   (b) it's a terminal *_extract (no further *_extract anywhere below) —
    #       used for generic blob chains where unblob couldn't name the fs type.
    extract_candidates: set[Path] = set()
    for dirpath, dirnames, _filenames in os.walk(extracted):
        d = Path(dirpath)
        if d == extracted:
            continue
        if len(d.relative_to(extracted).parts) > max_depth:
            dirnames[:] = []
            continue
        if d.name.endswith("_extract"):
            qualifies = (
                _has_known_fs_type_suffix(d.name)
                or not _has_extract_descendant(d)
            )
            if qualifies and any(c.is_dir() for c in d.iterdir()):
                extract_candidates.add(_find_fs_root(d))
                # Don't recurse into this candidate — its insides aren't
                # separate shards.
                dirnames[:] = []

    # Pass 2: score-based fallback for trees that don't use unblob's naming
    # (binwalk output, pre-extracted directories, etc.). Gate: skip anything
    # at or below an already-identified extract shard.
    score_candidates: set[Path] = set()
    for dirpath, dirnames, _filenames in os.walk(extracted):
        d = Path(dirpath)
        if d == extracted:
            continue
        if len(d.relative_to(extracted).parts) > max_depth:
            dirnames[:] = []
            continue
        if any(d == ec or _is_descendant(d, ec) for ec in extract_candidates):
            continue
        score, _ev = score_directory(d, dirnames, extraction_root=extracted)
        if score >= min_score:
            score_candidates.add(d)

    # Score every candidate, then keep the highest-scoring path in each
    # ancestor chain. Extract candidates get an unbeatable boost so they always
    # win against score-based candidates inside the same chain (though the
    # gate above already prevents most overlaps).
    EXTRACT_BOOST = 10_000
    scored: list[tuple[Path, int, dict, bool]] = []
    for p in extract_candidates | score_candidates:
        children = [c.name for c in p.iterdir() if c.is_dir()]
        s, ev = score_directory(p, children, extraction_root=extracted)
        is_extract = p in extract_candidates
        rank = s + (EXTRACT_BOOST if is_extract else 0)
        scored.append((p, rank, ev, is_extract))

    # Highest rank wins; tie-break by deeper path so descendants edge out parents.
    scored.sort(key=lambda c: (-c[1], -len(c[0].parts)))
    kept: list[tuple[Path, int, dict, bool]] = []
    for p, rank, ev, is_extract in scored:
        if any(_is_descendant(p, k[0]) or _is_descendant(k[0], p) for k in kept):
            continue
        kept.append((p, rank, ev, is_extract))

    results: list[tuple[Path, int, dict]] = []
    for p, rank, ev, is_extract in kept:
        score = rank - (EXTRACT_BOOST if is_extract else 0)
        results.append((p, score, ev))
    results.sort(key=lambda c: (-c[1], str(c[0])))
    return results


def _slugify(rel: Path) -> str:
    s = "__".join(rel.parts)
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("_")
    return s[:80] or "shard"


def tar_shards(
    shards: list[tuple[Path, int, dict]],
    extracted: Path,
    out_dir: Path,
    firmware_stem: str,
    scratch_root: Optional[Path] = None,
    reextract: bool = True,
    verbose: bool = False,
) -> list[ShardInfo]:
    """Tar each shard. When `reextract` is True and a shard's fs type has a
    native perm-preserving extractor available, the shard is re-extracted from
    its original blob before tarring (avoids 7z's permission corruption for
    cpio and similar). The re-extraction happens under `scratch_root`; if
    omitted, no re-extraction is attempted.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    infos: list[ShardInfo] = []
    for i, (path, score, ev) in enumerate(shards):
        rel = path.relative_to(extracted)
        slug = _slugify(rel)
        fs_type = _guess_fs_type(path, extracted)

        tar_source = path
        reextractor_used: Optional[str] = None
        blob_used: Optional[Path] = None
        if reextract and scratch_root is not None:
            tar_source, reextractor_used, blob_used = reextract_shard(
                path, fs_type, extracted, scratch_root, verbose=verbose,
            )

        tar_name = f"{firmware_stem}.shard.{i:02d}.{slug}.tar.gz"
        tar_path = out_dir / tar_name
        with tarfile.open(tar_path, "w:gz") as t:
            t.add(tar_source, arcname=".", recursive=True)
        infos.append(ShardInfo(
            name=tar_name, score=score, root_path=str(rel),
            fs_type_guess=fs_type,
            matched_root_dirs=ev.get("matched_root_dirs", []),
            matched_rootfs_files=ev.get("matched_files", []),
            file_count=ev.get("file_count", 0),
            reextracted_with=reextractor_used,
            source_blob=(str(blob_used.relative_to(extracted)) if blob_used else None),
        ))
    return infos


def write_manifest(infos: list[ShardInfo], out_dir: Path, firmware: Optional[Path], extractor: str) -> Path:
    manifest_path = out_dir / "shards.json"
    payload = {
        "firmware": firmware.name if firmware is not None else None,
        "firmware_stem": firmware.stem if firmware is not None else None,
        "extractor": extractor,
        "shards": [asdict(i) for i in infos],
    }
    with open(manifest_path, "w") as f:
        json.dump(payload, f, indent=2)
    return manifest_path


def load_manifest(shard_dir: Path) -> Optional[dict]:
    p = shard_dir / "shards.json"
    if not p.exists():
        return None
    with open(p, "r") as f:
        return json.load(f)


# --------------- Re-extraction (perm-preserving) ---------------

_GZIP_MAGIC = b"\x1f\x8b\x08"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_LZ4_MAGIC = b"\x04\x22\x4d\x18"

# cpio magic numbers (any of these means "this is a raw cpio archive").
_CPIO_MAGICS = (b"070701", b"070702", b"070707", b"\xc7\x71", b"\x71\xc7")


def _read_magic(path: Path, n: int = 8) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _is_cpio(magic: bytes) -> bool:
    return any(magic.startswith(m) for m in _CPIO_MAGICS)


def _decompress_pipeline(magic: bytes) -> Optional[list[str]]:
    """Return the decompression command for a wrapper format, or None if the
    blob is already raw. The command reads from stdin and writes to stdout.
    """
    if magic.startswith(_GZIP_MAGIC):
        return ["gunzip", "-c"]
    if magic.startswith(_BZIP2_MAGIC):
        return ["bunzip2", "-c"]
    if magic.startswith(_XZ_MAGIC):
        return ["unxz", "-c"]
    if magic.startswith(_LZ4_MAGIC) and _which("lz4"):
        return ["lz4", "-d", "-c"]
    return None


def reextract_cpio(blob_path: Path, out_dir: Path, verbose: bool = False) -> bool:
    """Re-extract a cpio (or gzipped/bzip2/xz-wrapped cpio) blob into out_dir
    using native cpio so permissions, setuid bits, and symlinks are preserved.

    Returns True on success, False if anything went wrong (caller falls back
    to the original 7z-extracted directory).
    """
    if not _which("cpio"):
        return False
    out_dir.mkdir(parents=True, exist_ok=True)
    magic = _read_magic(blob_path, 8)

    decomp = _decompress_pipeline(magic)
    cpio_cmd = ["cpio", "-idmu", "--no-absolute-filenames", "--quiet"]
    err_buf: bytes
    try:
        if decomp is not None:
            if not _which(decomp[0]):
                return False
            with open(blob_path, "rb") as src:
                p1 = subprocess.Popen(decomp, stdin=src, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
            p2 = subprocess.Popen(cpio_cmd, stdin=p1.stdout, cwd=out_dir,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if p1.stdout is not None:
                p1.stdout.close()
            _, err_buf = p2.communicate()
            p1.wait()
            ok = (p1.returncode == 0 and p2.returncode == 0)
        elif _is_cpio(magic):
            with open(blob_path, "rb") as src:
                r = subprocess.run(cpio_cmd, stdin=src, cwd=out_dir,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   check=False)
            err_buf = r.stderr
            ok = (r.returncode == 0)
        else:
            # Not a recognizable cpio (raw or compressed) — bail.
            return False
    except OSError as e:
        if verbose:
            print(f"[reextract] cpio failed for {blob_path}: {e}", file=sys.stderr)
        return False

    if not ok and verbose:
        print(f"[reextract] cpio non-zero exit for {blob_path}: {err_buf[:200]!r}", file=sys.stderr)
    # Even on partial success we want at least one extracted file for the
    # output to be useful; otherwise treat as failure.
    if ok:
        any_extracted = any(True for _ in out_dir.rglob("*"))
        return any_extracted
    return False


# Registry: maps reextractor key -> function (blob, out_dir, verbose) -> bool.
# Add entries here as new perm-preserving native extractors are needed.
REEXTRACTORS: dict[str, callable] = {
    "cpio": reextract_cpio,
}


def _find_extract_ancestor(path: Path, extraction_root: Path) -> Optional[Path]:
    """Walk up from `path` (inclusive) to find the first ancestor named
    `*_extract`. Returns None if there isn't one within extraction_root.
    """
    cur = path
    while cur != extraction_root and cur != cur.parent:
        if cur.name.endswith("_extract"):
            return cur
        cur = cur.parent
    return None


def _find_original_blob(extract_dir: Path) -> Optional[Path]:
    """Given a `<name>_extract` directory, return the path of the original
    blob `<name>` if it exists as a sibling. Tries `<name>` and `<name>.<ext>`
    variants for resilience.
    """
    parent = extract_dir.parent
    stem = extract_dir.name[: -len("_extract")]
    candidate = parent / stem
    if candidate.is_file():
        return candidate
    # Some extractors emit `<name>_extract` where the original was decompressed
    # into `<name>.uncompressed` first. We don't follow that chain here.
    return None


def reextract_shard(
    shard_path: Path,
    fs_type: Optional[str],
    extraction_root: Path,
    scratch_root: Path,
    verbose: bool = False,
) -> tuple[Path, Optional[str], Optional[Path]]:
    """If this shard's type has a known native re-extractor and we can locate
    the original blob, re-extract into a new directory under scratch_root and
    return that path. Otherwise return the original path.

    Returns (effective_path, reextractor_name_or_None, source_blob_or_None).
    """
    if fs_type is None or fs_type not in REEXTRACTOR_FOR_TYPE:
        return shard_path, None, None
    extractor_name = REEXTRACTOR_FOR_TYPE[fs_type]
    fn = REEXTRACTORS.get(extractor_name)
    if fn is None:
        return shard_path, None, None
    extract_dir = _find_extract_ancestor(shard_path, extraction_root)
    if extract_dir is None:
        return shard_path, None, None
    blob = _find_original_blob(extract_dir)
    if blob is None:
        return shard_path, None, None

    # Place the re-extraction under scratch_root so it gets cleaned up.
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(shard_path.relative_to(extraction_root)))[:120]
    out = scratch_root / "reextract" / f"{extractor_name}_{safe_slug}"
    if verbose:
        print(f"[reextract] {extractor_name}: {blob.name} -> {out}", file=sys.stderr)
    ok = fn(blob, out, verbose=verbose)
    if not ok:
        if verbose:
            print(f"[reextract] {extractor_name} failed; keeping 7z extraction at {shard_path}",
                  file=sys.stderr)
        return shard_path, None, None
    return out, extractor_name, blob


# --------------- Extractor invocation ---------------

class ExtractorMissing(RuntimeError):
    pass


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run_unblob(firmware: Path, scratch: Path, verbose: bool = False) -> Path:
    """Run unblob into scratch/unblob, return that directory."""
    if not _which("unblob"):
        raise ExtractorMissing(
            "unblob not found on PATH. Install it locally or run this inside "
            "the fw2tar Docker container where it's already available."
        )
    out = scratch / "unblob"
    out.mkdir(parents=True, exist_ok=True)
    # unblob's default --log is /<basename>.log which is at the filesystem
    # root and not writable for non-root users in the container. Pin it inside
    # the scratch dir instead.
    log_path = scratch / "unblob.log"
    cmd = ["unblob", "--extract-dir", str(out), "--log", str(log_path), str(firmware)]
    if verbose:
        print(f"[shard] running: {' '.join(cmd)}", file=sys.stderr)
    # unblob can be chatty even on success; suppress unless verbose.
    subprocess.run(
        cmd, check=True,
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.DEVNULL,
    )
    return out


def run_binwalk(firmware: Path, scratch: Path, verbose: bool = False) -> Path:
    """Run binwalk recursive extraction into scratch/binwalk."""
    if not _which("binwalk"):
        raise ExtractorMissing(
            "binwalk not found on PATH. Install it locally or run this inside "
            "the fw2tar Docker container where it's already available."
        )
    out = scratch / "binwalk"
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["binwalk", "--extract", "--matryoshka", "--directory", str(out), str(firmware)]
    if verbose:
        print(f"[shard] running: {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(
        cmd, check=True,
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.DEVNULL,
    )
    return out


# --------------- Top-level ---------------

def shard(
    firmware: Optional[Path],
    out_dir: Path,
    extractor: str = "unblob",
    extracted_dir: Optional[Path] = None,
    min_score: int = 3,
    reextract: bool = True,
    verbose: bool = False,
) -> dict:
    """Extract a firmware blob into per-shard .tar.gz files + a manifest.

    If `extracted_dir` is supplied, `firmware` may be None and the tree is
    walked directly. Re-extraction (e.g. native cpio for perm preservation)
    still works as long as the original blobs are present next to the
    *_extract dirs.

    Returns a dict summary suitable for printing.
    """
    cleanup_scratch: Path | None = None
    scratch_root: Path
    if extracted_dir is not None:
        if not extracted_dir.is_dir():
            raise FileNotFoundError(f"extracted_dir not found: {extracted_dir}")
        extraction_root = extracted_dir
        used_extractor = "preextracted"
        # Even with a pre-extracted tree we need a scratch dir for re-extraction.
        scratch_root = Path(tempfile.mkdtemp(prefix="fw2shard_"))
        cleanup_scratch = scratch_root
    else:
        if firmware is None or not firmware.is_file():
            raise FileNotFoundError(f"firmware not found: {firmware}")
        scratch_root = Path(tempfile.mkdtemp(prefix="fw2shard_"))
        cleanup_scratch = scratch_root
        if extractor == "unblob":
            extraction_root = run_unblob(firmware, scratch_root, verbose=verbose)
        elif extractor == "binwalk":
            extraction_root = run_binwalk(firmware, scratch_root, verbose=verbose)
        else:
            raise ValueError(f"unknown extractor: {extractor!r}")
        used_extractor = extractor

    # When --from-extracted was used we may not have a firmware path; pick a
    # stable stem from the extracted dir name so the per-shard tarball names
    # are deterministic.
    firmware_stem = firmware.stem if firmware is not None else extraction_root.resolve().name

    try:
        candidates = find_shards(extraction_root, min_score=min_score)
        if verbose:
            print(f"[shard] {len(candidates)} candidate fragment(s) selected", file=sys.stderr)
            for p, s, ev in candidates:
                print(f"  score={s:3d}  files={ev.get('file_count')}  "
                      f"{p.relative_to(extraction_root)}", file=sys.stderr)
        infos = tar_shards(
            candidates, extraction_root, out_dir, firmware_stem,
            scratch_root=scratch_root, reextract=reextract, verbose=verbose,
        )
        manifest_path = write_manifest(infos, out_dir, firmware, used_extractor)
        reextract_count = sum(1 for i in infos if i.reextracted_with)
        if verbose and reextract_count:
            print(f"[shard] re-extracted {reextract_count} shard(s) with native tools "
                  f"(perm-preserving)", file=sys.stderr)
        return {
            "shard_dir": str(out_dir),
            "manifest": str(manifest_path),
            "extractor": used_extractor,
            "reextracted_count": reextract_count,
            "count": len(infos),
            "shards": [asdict(i) for i in infos],
        }
    finally:
        if cleanup_scratch is not None:
            shutil.rmtree(cleanup_scratch, ignore_errors=True)
