# fw2tar stitch

LLM-driven filesystem stitching for firmware images that have more than one
filesystem in them.

`fw2tar` itself assumes a firmware blob contains one big rootfs and picks the
"best" candidate. That's wrong for plenty of real devices — e.g. the Rigol
MSO5000 (rootfs.img + an app UBIFS at `/rigol`), the D-Link DNS320 (cpio
ramdisk + a gzip-compressed config blob mounted at `/etc/NAS_CFG` + a squashfs
modules partition at `/usr/local/modules`), and most appliance-style firmware.
This tool extracts every filesystem fragment as a separate tarball ("shard"),
then drives a local LLM through the evidence (init scripts, fstab, dangling
symlinks, strings in binaries, fs-type from unblob's naming) to produce a
plan describing where each shard should be mounted. The plan can be reviewed
or auto-applied to produce a single unified `.rootfs.tar.gz`.

Local models in mind throughout: anything that speaks the OpenAI-compatible
HTTP API works (vllm, ollama, llama.cpp server, gpt-oss-120b, etc.). No
`langchain`. Two harness modes — native tool-calling and a JSON-emission
fallback — with auto-detection, so it stays robust across server quirks.


## Install

The stitcher and its Python deps (`openai`, `pydantic`, `pyyaml`) are baked
into the `rehosting/fw2tar` Docker image. The simplest invocation path is the
`./fwstitch` host wrapper, which mirrors `./fw2tar`:

```bash
./fwstitch shard ./firmware.bin -o ./shards
./fwstitch plan ./shards                     # auto-uses --network host
./fwstitch apply ./shards ./shards/stitch_plan.yaml --out ./fw.stitched.rootfs.tar.gz
./fwstitch all ./firmware.bin --shard-dir ./shards --out ./fw.stitched.rootfs.tar.gz
```

The wrapper auto-mounts any file/dir argument under `/host_<basename>` inside
the container, passes through the `LLM_*` env vars, and adds `--network host`
for `plan`/`all` so a local LLM server on the host is reachable.

You can also run it directly without the wrapper:

- **Inside the container**: `fwstitch <subcommand> ...` (on PATH already).
- **On the host**: `pip install -r fw2tar/utils/stitch/requirements.txt` and
  invoke `python -m stitch ...` (set `PYTHONPATH` to the parent of the
  `stitch/` directory, or `cd fw2tar/utils`).

Runtime deps (all already in the container, install on host as needed):

- Python 3.10+
- `openai` (any base URL — used for local servers too), `pydantic`, `pyyaml`
- For `shard`: `unblob` (preferred) or `binwalk` on PATH
- For perm-preserving re-extraction: `cpio` on PATH (plus `gunzip` /
  `bunzip2` / `unxz` / `lz4` if you have compressed-cpio blobs like
  `ramdisk_el`)
- `fakeroot` on PATH (the `shard` and `all` subcommands auto-re-exec under
  fakeroot so firmware uid/gid metadata survives — see "Ownership" below)


## Ownership: shard runs under fakeroot

Firmware images contain files owned by `root`, setuid binaries, and other
ownership metadata that must be preserved. unblob/binwalk extraction would
normally do `chown()` calls that need privilege; cpio does the same.

The `shard` and `all` subcommands automatically re-exec themselves under
`fakeroot --` if they're not already inside one (and you're not root).
fakeroot intercepts those calls, records the intended uid/gid in shadow
metadata, and when the resulting tree is tarred up the headers reflect what
the firmware actually wanted. The `plan` and `apply` subcommands are
read-only / tar-header-only and don't need fakeroot.

To opt out (e.g. when debugging with everything owned by your real uid), pass
`--no-fakeroot`. If `fakeroot` isn't on PATH, a loud warning is printed and
extraction proceeds with your real uid — useful for trials, harmful for any
firmware you actually plan to boot.


## Three steps: shard, plan, apply

```
firmware blob ──[shard]──> shard_dir/ ──[plan]──> stitch_plan.yaml ──[apply]──> stitched.rootfs.tar.gz
                            (N tarballs               (LLM-produced               (one unified rootfs)
                             + shards.json)            stitching plan)
```

You can also collapse to one command with `all`.


### shard — extract every filesystem fragment

```bash
python -m utils.stitch shard firmware.bin -o ./shards
```

Runs unblob into a temp directory, walks the extraction tree, and emits one
`.tar.gz` per detected filesystem fragment plus a `shards.json` manifest with
provenance. Robust to "piles of shards" — there's no `--primary-limit`
or "is this root-like?" filter; every leaf of the extraction tree that looks
like a filesystem comes through as its own tarball.

Selection logic, in priority order:

1. **Terminal `*_extract` directories** (unblob's naming). When unblob finishes
   extracting a chunk it produces `foo.<fs>_extract/` next to `foo.<fs>`. The
   suffix encodes the fs type (`ubifs_extract`, `squashfs_v4_le_extract`,
   `jffs2_extract`, `cpio_extract`, `gzip_extract`, etc.) — that flows into
   the manifest as `fs_type_guess` and the LLM sees it as a strong hint.
   Wrapper directories like `squashfs-root/` are descended automatically.
2. **Score-based fallback.** For trees that don't use unblob's naming (binwalk
   output, pre-extracted directories), each directory gets a score from
   filesystem-like signals (top-level `bin`/`etc`/`sbin`/..., presence of
   `/bin/sh`, `/etc/passwd`, `/sbin/init`, etc.). The highest-scoring path in
   each ancestor chain wins.

#### Native re-extract for cpio (and similar)

unblob and binwalk both delegate cpio extraction to 7z, which **does not**
preserve setuid bits, restrictive permissions, or sometimes even symlinks.
The shard step automatically detects cpio shards (by `fs_type_guess`), locates
the original blob next to the `*_extract` directory, and re-extracts with
native `cpio -idmu --no-absolute-filenames`. Compressed-cpio wrappers
(`gunzip|cpio`, `bunzip2|cpio`, `unxz|cpio`, `lz4 -d|cpio`) are auto-detected
by magic bytes.

If `cpio` isn't on PATH or the re-extract fails, the 7z output is used
unchanged and the manifest records `reextracted_with: null` for that shard
(visible to the LLM). The mapping lives in `shard.py`:

```python
REEXTRACTOR_FOR_TYPE = {"cpio": "cpio"}
REEXTRACTORS         = {"cpio": reextract_cpio}
```

Add new entries here for any other format where the upstream extractor is
lossy.

#### Flags

```
python -m utils.stitch shard FIRMWARE -o OUT
  [--extractor unblob|binwalk]    # default unblob
  [--from-extracted DIR]          # skip extraction, walk a pre-extracted tree
  [--min-score 3]                 # score-pass floor; only matters when *_extract isn't used
  [--no-reextract]                # keep 7z's broken-perms output (debug only)
  [-v]                            # log every candidate + each reextract
```


### plan — LLM produces a stitch plan

```bash
export LLM_BASE_URL=http://localhost:8000/v1
export LLM_API_KEY=dummy
export LLM_MODEL=gpt-oss-120b
python -m utils.stitch plan ./shards
# writes ./shards/stitch_plan.yaml
```

The harness:

1. Loads every shard tarball into a `FragmentCache`.
2. Pre-digests each shard via `fs_summary` (rootfs file presence, top-dir
   counts, fs_type_guess from the manifest, unblob root path, score,
   reextracted_with) and injects that into the initial prompt so the LLM
   doesn't burn turns asking for the obvious.
3. Loops, exposing six read-only tools the LLM can call to gather more
   evidence:
   - `list_paths(fragment, pattern)` — glob inside the shard
   - `read_file(fragment, path, max_bytes)` — for `/etc/fstab`, init scripts
   - `grep_in_fragment(fragment, pattern, path_glob)` — find `mount` commands
   - `strings_of(fragment, path)` — paths hardcoded in init binaries
   - `find_dangling_symlinks(fragment)` — absolute symlinks whose target is
     missing in this shard (strongest cross-fragment signal)
   - `fs_summary(fragment)` — the precomputed digest
4. The LLM terminates by calling `submit_plan` with a `StitchPlan`. The
   harness validates the plan against the pydantic schema; failures are
   reported back to the model and it retries up to a bounded number of times.
5. On the last turn `tool_choice` is forced to `submit_plan` so the loop
   always exits with a plan (which may be low-confidence).

#### Native tool-calling vs JSON fallback

Most modern servers (recent ollama, vllm, llama.cpp) support OpenAI-style
tool calling. The harness uses it by default. If the server rejects the
`tools` parameter, or returns empty `tool_calls` twice in a row, the harness
transparently switches to a JSON-emission fallback where the model emits
exactly one of:

```
{"tool": "<name>", "args": { ... }}
{"final": { ...StitchPlan... }}
```

per turn. Pass `--no-native-tools` to force this mode from the start.

#### Flags

```
python -m utils.stitch plan SHARD_DIR
  [--plan-out plan.yaml]   # default: <shard_dir>/stitch_plan.yaml
  [--model NAME]           # else $LLM_MODEL
  [--base-url URL]         # else $LLM_BASE_URL
  [--api-key KEY]          # else $LLM_API_KEY, defaults to 'dummy'
  [--max-turns 10]
  [--no-native-tools]
  [-k] [--insecure]        # skip TLS cert verification (self-signed local server)
  [-v]                     # log each turn + every tool call/result
```

If you're hitting a self-hosted model over HTTPS with a self-signed cert
(common on internal vllm / llama.cpp deployments), pass `-k` (or
`--insecure`), or set `OPENAI_INSECURE=1`. Same idea as `curl -k`.


### apply — build the stitched rootfs

```bash
python -m utils.stitch apply ./shards ./shards/stitch_plan.yaml \
    --out ./shards/firmware.stitched.rootfs.tar.gz
```

Streams each shard's tar members into a single output `.tar.gz`, rewriting
paths to sit under the chosen mount point and preserving mode / uid / gid /
mtime / symlinks (no re-tar-from-disk; permissions never round-trip through
the filesystem). The fw2tar metadata trailer (`stitched_from: [...]`, plan
hash, confidence) is appended so `fw2tar/utils/show_metadata.py` can still
read the output.

Mount semantics:

- Base shard is processed first (its members land at `/`).
- Overlays are applied in plan order. **Absolute symlink targets in overlays
  are left unchanged** — they're meant to resolve in the unified rootfs
  view, which is what the original firmware author intended.
- Path collisions: default policy is `overlay` wins (matches union-mount
  intuition). `--strict` errors on collision instead. Sample collisions are
  always printed to stderr.
- `confidence: low` plans are refused unless `--force`.

#### Flags

```
python -m utils.stitch apply SHARD_DIR PLAN_YAML
  [--out PATH]
  [--on-conflict {base,overlay,error}]   # default overlay
  [--strict]                              # alias for --on-conflict error
  [--force]                               # apply even if confidence=low
  [-v]
```


### all — shard → plan → apply, end-to-end

```bash
python -m utils.stitch all firmware.bin --shard-dir ./shards --out ./firmware.stitched.rootfs.tar.gz
```

Same args as the three commands combined. Pass `--no-apply` to stop after
the plan (useful in CI where a human reviews before commit).


## Environment variables

All consumed by the `plan` step; CLI flags override.

| Variable           | Meaning                                                    |
| ------------------ | ---------------------------------------------------------- |
| `LLM_BASE_URL`  | LLM server endpoint, e.g. `http://localhost:8000/v1`       |
| `LLM_API_KEY`   | API key; defaults to `"dummy"` since local servers ignore  |
| `LLM_MODEL`     | Model name, e.g. `gpt-oss-120b`, `gemma3:27b`, `qwen2.5:32b` |
| `OPENAI_INSECURE`  | `1` to skip TLS verification (same as `-k` / `--insecure`)  |


## File formats

### `shards.json` (manifest produced by `shard`)

```yaml
firmware: dns320_fw.bin
firmware_stem: dns320_fw
extractor: unblob          # or binwalk, or preextracted
shards:
  - name: dns320_fw.shard.00.firmware.bin_extract__ramdisk_el_extract.tar.gz
    score: 46              # higher = more rootfs-like
    root_path: firmware.bin_extract/ramdisk_el_extract
    fs_type_guess: cpio
    matched_root_dirs: [bin, etc, lib, sbin, usr]
    matched_rootfs_files: [etc/passwd, sbin/init, bin/sh]
    file_count: 1247
    reextracted_with: cpio                                    # null if not re-extracted
    source_blob: firmware.bin_extract/ramdisk_el              # the original blob, if known
  - name: dns320_fw.shard.01.firmware.bin_extract__default_gzip_extract__NAS_CFG.tar.gz
    score: 5
    root_path: firmware.bin_extract/default_gzip_extract/NAS_CFG
    fs_type_guess: gzip
    ...
```

### `stitch_plan.yaml` (produced by `plan`, consumed by `apply`)

```yaml
fragments:
  - source: dns320_fw.shard.00.firmware.bin_extract__ramdisk_el_extract.tar.gz
    mount_point: /
    role: base
    fs_type: cpio
    notes: contains /bin/sh, /sbin/init; /etc/init.d/rcS mounts the others
  - source: dns320_fw.shard.01.firmware.bin_extract__default_gzip_extract__NAS_CFG.tar.gz
    mount_point: /etc/NAS_CFG
    role: overlay
    fs_type: gzip
  - source: dns320_fw.shard.02.firmware.bin_extract__modules.squashfs_v4_le_extract__modules.tar.gz
    mount_point: /usr/local/modules
    role: overlay
    fs_type: squashfs
reasoning: |
  Base picked from the cpio ramdisk — it's the only fragment with /sbin/init
  and /etc/passwd. /etc/init.d/rcS references /etc/NAS_CFG and
  /usr/local/modules; those are missing in the base but provided by the
  other two shards.
confidence: high
open_questions: []
```

Validation rules (enforced by pydantic):

- exactly one fragment with `role: base`
- the base must have `mount_point: /`
- `mount_point`s are unique and absolute
- `mount_point`s are normalized (e.g. `/foo//` -> `/foo`)


## Architectural notes

- The whole stitch package is stdlib + `openai` + `pydantic` + `pyyaml`. No
  langchain. The "agent" is ~250 lines of `harness.py`.
- Tool outputs are capped (`max_bytes`, `max_hits`, member-name caps) so the
  10-turn loop fits in a 16k context on small local models.
- `tar`-time path rewriting: each shard's members are streamed straight from
  the input `.tar.gz` into the output with their `name` prefixed by the
  mount point. We never extract to disk, so perms/uid/gid/mtime/symlinks
  pass through losslessly.
- The metadata trailer is appended as a second gzip member; gzip's
  multi-member format means `fw2tar/utils/show_metadata.py` reads stitched
  outputs unchanged.


## Limits / gotchas

- The plan currently models exactly one base. Multi-base layouts (e.g. dual-
  firmware images, A/B partitions where both are usable rootfs) would need a
  schema change.
- `--from-extracted` works for re-extraction only if the original blobs are
  still next to the `*_extract` directories. If you've deleted them, run the
  shard step against the firmware blob instead.
- The `fs_type_guess` is best-effort; it comes from unblob's naming.
  Pre-extracted trees may have `fs_type_guess: null` and the LLM falls back
  to other evidence.
- Low-confidence plans (`confidence: low`) refuse to `--apply` without
  `--force`. Usually that's the harness telling you it didn't find enough
  signal — check `open_questions`, the verbose log, or hand-edit the YAML.
- Empty shard directories produce a hard error. One-shard directories produce
  a warning (stitching would be a no-op).
- The harness sends a 1-token completion at startup to verify the endpoint is
  reachable. If your server has long cold-start times, increase
  `request_timeout` in `HarnessConfig` (not currently exposed via CLI).


## Hacking

Module layout:

```
fw2tar/utils/stitch/
  __main__.py        # python -m utils.stitch entry point
  cli.py             # argparse, subcommand dispatch
  shard.py           # extractor invocation, candidate selection, re-extract
  harness.py         # tool-use loop (native + JSON-fallback modes)
  tools.py           # the six LLM-callable tools + FragmentCache
  prompts.py         # SYSTEM_PROMPT and friends (terse on purpose)
  plan.py            # StitchPlan schema, yaml IO, apply_plan()
  requirements.txt
  README.md          # this file
```

Adding a new tool the LLM can call: write a pydantic args model + a function
in `tools.py`, append to the `TOOLS` list. The schema is auto-projected into
the OpenAI tools array and into the JSON-fallback prompt.

Adding a perm-preserving re-extractor: write a function with signature
`(blob: Path, out_dir: Path, verbose: bool) -> bool` in `shard.py`, register
it in `REEXTRACTORS`, and map its fs type in `REEXTRACTOR_FOR_TYPE`. The
shard step handles the rest.

Adding a new fs-type guess from extraction-dir naming: append to
`EXTRACT_SUFFIX_TYPES` in `shard.py` (longest suffix first).


## Testing without a real LLM

The schema, apply path, shard selection, and cpio re-extract are all
exercisable without an LLM or pydantic. The repository already includes
ad-hoc smoke tests as shell one-liners in the commit history; promote them
to `fw2tar/utils/stitch/tests/` if you want to wire them into CI.

Quick manual e2e (requires unblob + cpio + a local LLM):

```bash
python -m utils.stitch all path/to/MSO5000.bin --shard-dir /tmp/mso5k -v
# inspect /tmp/mso5k/stitch_plan.yaml
# the resulting /tmp/mso5k/mso5k.stitched.rootfs.tar.gz should mount the
# UBIFS app partition at /rigol and the rootfs at /
```
