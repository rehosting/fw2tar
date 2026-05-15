"""Tools the LLM can call to inspect filesystem fragments.

Each tool: a pydantic args model + a function that takes the FragmentCache and
returns JSON-serializable output. The TOOLS registry is projected into OpenAI
tool schemas at startup. All tools enforce caps so context stays bounded on
small local models.
"""
from __future__ import annotations

import fnmatch
import json
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field


# fw2tar's per-extractor output naming: <fwname>.<extractor>.<idx>.tar.gz
_FW2TAR_NAME_RE = re.compile(r"^(?P<fw>.+?)\.(?P<extractor>binwalk|binwalkv3|binwalk3|unblob)\.(?P<idx>\d+)\.tar\.gz$")

# The shard step's output naming: <fwname>.shard.<NN>.<slug>.tar.gz
_SHARD_NAME_RE = re.compile(r"^(?P<fw>.+?)\.shard\.(?P<idx>\d+)\.(?P<slug>.+)\.tar\.gz$")


@dataclass
class FragmentInfo:
    name: str
    extractor: str | None
    index: int | None
    path: Path
    size: int
    # Populated from shards.json when the fragment dir was produced by the
    # shard step. Strongest signals for the LLM live here.
    fs_type_guess: str | None = None
    root_path: str | None = None
    matched_root_dirs: list[str] = field(default_factory=list)
    matched_rootfs_files: list[str] = field(default_factory=list)
    shard_score: int | None = None
    file_count: int | None = None
    reextracted_with: str | None = None


def _load_manifest(frag_dir: Path) -> dict[str, dict]:
    """Return shards.json keyed by shard name, or {} if no manifest."""
    p = frag_dir / "shards.json"
    if not p.exists():
        return {}
    try:
        with open(p, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {s["name"]: s for s in data.get("shards", [])}


class FragmentCache:
    """Owns open TarFile handles, keyed by fragment basename."""

    def __init__(self, frag_dir: Path):
        self.frag_dir = frag_dir
        self._infos: dict[str, FragmentInfo] = {}
        self._tars: dict[str, tarfile.TarFile] = {}
        self._names: dict[str, list[str]] = {}
        manifest = _load_manifest(frag_dir)
        for p in sorted(frag_dir.iterdir()):
            if not p.is_file() or not p.name.endswith(".tar.gz"):
                continue
            if ".rootfs." in p.name and ".stitched." not in p.name:
                # Skip fw2tar's final selected output; we want the raw per-extractor or shard pieces.
                continue
            extractor = None
            idx = None
            m = _SHARD_NAME_RE.match(p.name)
            if m:
                extractor = "shard"
                idx = int(m.group("idx"))
            else:
                m2 = _FW2TAR_NAME_RE.match(p.name)
                if m2:
                    extractor = m2.group("extractor")
                    idx = int(m2.group("idx"))
            info = FragmentInfo(
                name=p.name, extractor=extractor, index=idx,
                path=p, size=p.stat().st_size,
            )
            meta = manifest.get(p.name)
            if meta:
                info.fs_type_guess = meta.get("fs_type_guess")
                info.root_path = meta.get("root_path")
                info.matched_root_dirs = list(meta.get("matched_root_dirs") or [])
                info.matched_rootfs_files = list(meta.get("matched_rootfs_files") or [])
                info.shard_score = meta.get("score")
                info.file_count = meta.get("file_count")
                info.reextracted_with = meta.get("reextracted_with")
            self._infos[p.name] = info

    def names(self) -> list[str]:
        return list(self._infos.keys())

    def info(self, name: str) -> FragmentInfo:
        if name not in self._infos:
            raise KeyError(f"unknown fragment: {name!r} (known: {list(self._infos)})")
        return self._infos[name]

    def tar(self, name: str) -> tarfile.TarFile:
        if name not in self._tars:
            self._tars[name] = tarfile.open(self.info(name).path, "r:*")
        return self._tars[name]

    def member_names(self, name: str) -> list[str]:
        if name not in self._names:
            self._names[name] = self.tar(name).getnames()
        return self._names[name]

    def close(self):
        for t in self._tars.values():
            try:
                t.close()
            except Exception:
                pass


# ---------- Args models ----------

class ListPathsArgs(BaseModel):
    fragment: str
    pattern: str = Field(description="glob pattern, e.g. 'etc/*' or '**/init*'")
    max: int = Field(default=50, ge=1, le=500)


class ReadFileArgs(BaseModel):
    fragment: str
    path: str
    max_bytes: int = Field(default=4096, ge=1, le=32768)


class GrepArgs(BaseModel):
    fragment: str
    pattern: str = Field(description="regex (python re) matched per line")
    path_glob: str = Field(default="etc/**")
    max_hits: int = Field(default=20, ge=1, le=200)


class StringsArgs(BaseModel):
    fragment: str
    path: str
    min_len: int = Field(default=6, ge=3, le=64)
    max_hits: int = Field(default=80, ge=1, le=500)


class FragmentArgs(BaseModel):
    fragment: str
    max: int = Field(default=30, ge=1, le=200)


class FragmentOnlyArgs(BaseModel):
    fragment: str


# ---------- Helpers ----------

def _normalize(name: str) -> str:
    n = name.lstrip("./")
    return n


def _resolve_member(tf: tarfile.TarFile, path: str) -> tarfile.TarInfo | None:
    """Find a member by relaxed path lookup. Tarballs may store names as './foo'."""
    candidates = [path, "./" + path.lstrip("/"), path.lstrip("/")]
    for c in candidates:
        try:
            return tf.getmember(c)
        except KeyError:
            continue
    norm = _normalize(path)
    for m in tf.getmembers():
        if _normalize(m.name) == norm:
            return m
    return None


def _glob_paths(names: list[str], pattern: str, limit: int) -> list[str]:
    norm_names = [_normalize(n) for n in names]
    matched: list[str] = []
    if "**" in pattern:
        # fnmatch doesn't handle ** — convert to a regex.
        regex_pat = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", ".")
        rx = re.compile("^" + regex_pat + "$")
        for n in norm_names:
            if rx.match(n):
                matched.append(n)
                if len(matched) >= limit:
                    break
    else:
        for n in norm_names:
            if fnmatch.fnmatchcase(n, pattern):
                matched.append(n)
                if len(matched) >= limit:
                    break
    return matched


def _read_member_bytes(tf: tarfile.TarFile, ti: tarfile.TarInfo, max_bytes: int) -> bytes:
    f = tf.extractfile(ti)
    if f is None:
        return b""
    data = f.read(max_bytes + 1)
    return data


def _safe_decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


# ---------- Tool implementations ----------

def tool_list_paths(cache: FragmentCache, args: ListPathsArgs) -> dict:
    names = cache.member_names(args.fragment)
    hits = _glob_paths(names, args.pattern, args.max)
    return {"fragment": args.fragment, "pattern": args.pattern, "count": len(hits), "paths": hits}


def tool_read_file(cache: FragmentCache, args: ReadFileArgs) -> dict:
    tf = cache.tar(args.fragment)
    ti = _resolve_member(tf, args.path)
    if ti is None:
        return {"fragment": args.fragment, "path": args.path, "error": "not found"}
    if ti.issym() or ti.islnk():
        return {
            "fragment": args.fragment, "path": args.path,
            "symlink_to": ti.linkname, "size": 0, "truncated": False, "content": "",
        }
    if not ti.isreg():
        return {"fragment": args.fragment, "path": args.path, "error": f"not a regular file (type={ti.type!r})"}
    data = _read_member_bytes(tf, ti, args.max_bytes)
    truncated = len(data) > args.max_bytes
    data = data[: args.max_bytes]
    return {
        "fragment": args.fragment, "path": args.path,
        "size": ti.size, "mode": oct(ti.mode), "truncated": truncated,
        "content": _safe_decode(data),
    }


def tool_grep(cache: FragmentCache, args: GrepArgs) -> dict:
    tf = cache.tar(args.fragment)
    names = cache.member_names(args.fragment)
    try:
        rx = re.compile(args.pattern)
    except re.error as e:
        return {"error": f"bad regex: {e}"}
    candidate_paths = _glob_paths(names, args.path_glob, limit=500)
    hits: list[dict] = []
    for p in candidate_paths:
        if len(hits) >= args.max_hits:
            break
        ti = _resolve_member(tf, p)
        if ti is None or not ti.isreg():
            continue
        if ti.size > 256 * 1024:
            continue
        data = _read_member_bytes(tf, ti, 256 * 1024)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append({"path": p, "line_no": i, "line": line[:240]})
                if len(hits) >= args.max_hits:
                    break
    return {
        "fragment": args.fragment, "pattern": args.pattern,
        "path_glob": args.path_glob, "count": len(hits), "hits": hits,
    }


_STRINGS_RE = re.compile(rb"[\x20-\x7e]{%d,}")


def tool_strings(cache: FragmentCache, args: StringsArgs) -> dict:
    tf = cache.tar(args.fragment)
    ti = _resolve_member(tf, args.path)
    if ti is None:
        return {"error": "not found", "fragment": args.fragment, "path": args.path}
    if not ti.isreg():
        return {"error": "not a regular file", "fragment": args.fragment, "path": args.path}
    rx = re.compile(rb"[\x20-\x7e]{%d,}" % args.min_len)
    data = _read_member_bytes(tf, ti, 2 * 1024 * 1024)
    hits = []
    for m in rx.finditer(data):
        s = m.group(0).decode("ascii", errors="replace")
        # Bias toward strings that look like paths or mount-related tokens.
        if "/" in s or any(tok in s for tok in ("mount", "/dev/", "/etc/", "/var/", "/usr/", "/opt/", "fstab", ".sh")):
            hits.append(s)
        if len(hits) >= args.max_hits:
            break
    return {
        "fragment": args.fragment, "path": args.path,
        "count": len(hits), "strings": hits,
    }


def tool_find_dangling_symlinks(cache: FragmentCache, args: FragmentArgs) -> dict:
    tf = cache.tar(args.fragment)
    names_set = set(_normalize(n) for n in cache.member_names(args.fragment))
    hits = []
    for ti in tf.getmembers():
        if not (ti.issym() or ti.islnk()):
            continue
        target = ti.linkname
        if not target.startswith("/"):
            continue
        rel = target.lstrip("/")
        if rel not in names_set:
            hits.append({"link": _normalize(ti.name), "target": target})
            if len(hits) >= args.max:
                break
    return {"fragment": args.fragment, "count": len(hits), "dangling": hits}


_KEY_CHECKS = {
    "has_etc_passwd": "etc/passwd",
    "has_sbin_init": "sbin/init",
    "has_bin_sh": "bin/sh",
    "has_lib_ld": "lib",  # checked specially below
    "has_etc_fstab": "etc/fstab",
    "has_etc_inittab": "etc/inittab",
    "has_etc_init_d_rcS": "etc/init.d/rcS",
}


def tool_fs_summary(cache: FragmentCache, args: FragmentOnlyArgs) -> dict:
    tf = cache.tar(args.fragment)
    names = cache.member_names(args.fragment)
    norm = [_normalize(n) for n in names]
    norm_set = set(norm)
    result: dict[str, Any] = {"fragment": args.fragment}
    for k, p in _KEY_CHECKS.items():
        if k == "has_lib_ld":
            result[k] = any(n.startswith("lib/ld-") or n.startswith("lib/ld.") or n == "lib/ld-linux.so" for n in norm)
        else:
            result[k] = p in norm_set or any(n == p for n in norm)
    # top-level directory counts
    counts: dict[str, int] = {}
    for n in norm:
        head = n.split("/", 1)[0] if "/" in n else n
        counts[head] = counts.get(head, 0) + 1
    top_dirs = sorted(counts.items(), key=lambda x: -x[1])[:12]
    info = cache.info(args.fragment)
    result["top_dirs"] = [{"name": d, "count": c} for d, c in top_dirs]
    result["extractor"] = info.extractor
    result["index"] = info.index
    result["compressed_size"] = info.size
    # Manifest-derived provenance (present when this came from the shard step).
    if info.fs_type_guess is not None:
        result["fs_type_guess"] = info.fs_type_guess
    if info.root_path is not None:
        result["root_path"] = info.root_path
    if info.shard_score is not None:
        result["shard_score"] = info.shard_score
    if info.file_count is not None:
        result["manifest_file_count"] = info.file_count
    if info.reextracted_with is not None:
        result["reextracted_with"] = info.reextracted_with
    return result


# ---------- Registry ----------

@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[[FragmentCache, BaseModel], dict]


TOOLS: list[Tool] = [
    Tool(
        name="list_paths",
        description=(
            "List member paths in a fragment matching a glob pattern. "
            "Use to check whether key files exist (e.g. pattern='etc/fstab', 'sbin/init', '**/rcS')."
        ),
        args_model=ListPathsArgs,
        fn=tool_list_paths,
    ),
    Tool(
        name="read_file",
        description=(
            "Read a small text file from a fragment (up to max_bytes). "
            "Use for /etc/fstab, /etc/init.d/rcS, /etc/inittab, /etc/rc.local, /etc/profile."
        ),
        args_model=ReadFileArgs,
        fn=tool_read_file,
    ),
    Tool(
        name="grep_in_fragment",
        description=(
            "Run a regex across small text files in a fragment under path_glob. "
            "Use to find 'mount' commands or hardcoded mount paths in init scripts."
        ),
        args_model=GrepArgs,
        fn=tool_grep,
    ),
    Tool(
        name="strings_of",
        description=(
            "Extract printable strings biased toward paths from a binary inside a fragment. "
            "Use on /sbin/init, /bin/busybox, or service binaries when init scripts are unhelpful."
        ),
        args_model=StringsArgs,
        fn=tool_strings,
    ),
    Tool(
        name="find_dangling_symlinks",
        description=(
            "List absolute symlinks in a fragment whose target does not exist inside the same fragment. "
            "Strongest cross-fragment-dependency signal."
        ),
        args_model=FragmentArgs,
        fn=tool_find_dangling_symlinks,
    ),
    Tool(
        name="fs_summary",
        description=(
            "Summarize a fragment: presence of key root-fs files plus top-level directory counts."
        ),
        args_model=FragmentOnlyArgs,
        fn=tool_fs_summary,
    ),
]

TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def to_openai_schemas(plan_schema: dict) -> list[dict]:
    """Return the OpenAI 'tools' array including submit_plan."""
    out: list[dict] = []
    for t in TOOLS:
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.args_model.model_json_schema(),
            },
        })
    out.append({
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": (
                "Submit your final StitchPlan. The harness will validate it and the loop ends on success."
            ),
            "parameters": plan_schema,
        },
    })
    return out
