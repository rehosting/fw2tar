"""StitchPlan schema, yaml IO, and apply().

A StitchPlan describes how to merge multiple filesystem fragments (each one a
.tar.gz produced by fw2tar) into a single unified rootfs tarball. One fragment
is the "base" mounted at /, the rest are "overlays" mounted at sub-paths.

apply_plan() streams members from each input tar, rewrites their paths to sit
under the chosen mount point, and writes a single gzipped output tar that
preserves permissions, ownership, mtimes, and symlinks.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import posixpath
import sys
import tarfile
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class Fragment(BaseModel):
    source: str
    mount_point: str
    role: Literal["base", "overlay"]
    fs_type: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def normalize(self):
        mp = self.mount_point
        if not mp.startswith("/"):
            raise ValueError(f"mount_point must be absolute: {mp!r}")
        self.mount_point = posixpath.normpath(mp)
        return self


class StitchPlan(BaseModel):
    fragments: list[Fragment] = Field(min_length=1)
    reasoning: str
    confidence: Literal["low", "medium", "high"]
    open_questions: list[str] = []

    @model_validator(mode="after")
    def one_base(self):
        bases = [f for f in self.fragments if f.role == "base"]
        if len(bases) != 1:
            raise ValueError(f"plan must have exactly one base fragment, got {len(bases)}")
        if bases[0].mount_point != "/":
            raise ValueError(f"base fragment must be mounted at /, got {bases[0].mount_point!r}")
        seen_mounts: set[str] = set()
        for f in self.fragments:
            if f.mount_point in seen_mounts:
                raise ValueError(f"duplicate mount_point: {f.mount_point}")
            seen_mounts.add(f.mount_point)
        return self


def dump_plan(plan: StitchPlan, path: Path) -> None:
    data = plan.model_dump(exclude_none=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def load_plan(path: Path) -> StitchPlan:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return StitchPlan.model_validate(data)


def plan_hash(plan: StitchPlan) -> str:
    canonical = json.dumps(plan.model_dump(), sort_keys=True).encode()
    return hashlib.sha1(canonical).hexdigest()


def _rewrite_path(mount_point: str, name: str) -> str:
    name = name.lstrip("./")
    if mount_point == "/":
        joined = "/" + name
    else:
        joined = mount_point.rstrip("/") + "/" + name
    return posixpath.normpath(joined).lstrip("/")


def _rewrite_linkname(mount_point: str, linkname: str) -> str:
    # Absolute symlink targets are left alone: at runtime they resolve against
    # the unified rootfs view, which is exactly what the original firmware
    # author intended when the partition was mounted at <mount_point>. Relative
    # targets are unchanged since they resolve relative to the link's location.
    return linkname


def apply_plan(
    plan: StitchPlan,
    frag_dir: Path,
    out_path: Path,
    on_conflict: Literal["base", "overlay", "error"] = "overlay",
    verbose: bool = False,
) -> dict:
    """Produce a single stitched .tar.gz from the plan.

    Returns a stats dict with conflict counts, members written, and the plan
    hash. on_conflict controls which side wins when two fragments place a
    member at the same path: "base" keeps the first occurrence (base is
    processed first), "overlay" keeps the last (matches union-mount intuition),
    "error" raises.
    """
    ordered = sorted(plan.fragments, key=lambda f: 0 if f.role == "base" else 1)

    seen: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    members_written = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    with tarfile.open(tmp_path, "w:gz") as out_tar:
        for frag in ordered:
            src = frag_dir / frag.source
            if not src.exists():
                raise FileNotFoundError(f"fragment not found: {src}")
            if verbose:
                print(f"[apply] {frag.source} ({frag.role}) -> {frag.mount_point}", file=sys.stderr)
            with tarfile.open(src, "r:*") as in_tar:
                for ti in in_tar:
                    new_name = _rewrite_path(frag.mount_point, ti.name)
                    if not new_name:
                        continue
                    if new_name in seen:
                        conflicts.append((new_name, seen[new_name], frag.source))
                        if on_conflict == "error":
                            raise RuntimeError(
                                f"path collision at {new_name}: {seen[new_name]} vs {frag.source}"
                            )
                        if on_conflict == "base":
                            continue
                    new_ti = tarfile.TarInfo(name=new_name)
                    new_ti.size = ti.size
                    new_ti.mode = ti.mode
                    new_ti.uid = ti.uid
                    new_ti.gid = ti.gid
                    new_ti.uname = ti.uname
                    new_ti.gname = ti.gname
                    new_ti.mtime = ti.mtime
                    new_ti.type = ti.type
                    new_ti.linkname = _rewrite_linkname(frag.mount_point, ti.linkname) if ti.linkname else ""
                    new_ti.devmajor = ti.devmajor
                    new_ti.devminor = ti.devminor
                    if ti.isreg():
                        f = in_tar.extractfile(ti)
                        out_tar.addfile(new_ti, fileobj=f)
                    else:
                        out_tar.addfile(new_ti)
                    seen[new_name] = frag.source
                    members_written += 1

    # fw2tar metadata trailer — see show_metadata.py and src/archive.rs. The
    # trailer lives in the *decompressed* gzip view, after the tar EOF blocks.
    # gzip supports multi-member concatenation, so we append a second gzip
    # member that decompresses to: 16 nulls + json + "made with fw2tar".
    metadata = {
        "file": str(out_path.name),
        "fw2tar_command": "stitch (fw2tar.utils.stitch)",
        "input_hash": plan_hash(plan),
        "stitched_from": [f.source for f in plan.fragments],
        "stitch_plan_confidence": plan.confidence,
    }
    # Note the "\n" separator: show_metadata.py does string.split("\n") to split
    # the json blob from the magic. archive.rs omits it (latent inconsistency
    # between fw2tar and its own utility); we match show_metadata.py here so the
    # existing tool keeps working on stitched outputs.
    with open(tmp_path, "ab") as f, gzip.GzipFile(fileobj=f, mode="wb") as g:
        g.write(b"\x00" * 0x10)
        g.write(json.dumps(metadata).encode())
        g.write(b"\nmade with fw2tar")

    tmp_path.rename(out_path)

    return {
        "members_written": members_written,
        "conflicts": len(conflicts),
        "conflict_samples": conflicts[:10],
        "plan_hash": plan_hash(plan),
        "out_path": str(out_path),
    }
