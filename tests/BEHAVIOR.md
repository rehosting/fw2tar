# fw2tar behavior contract & characterization

This document records **what the fw2tar pipeline guarantees today**, so that the
forthcoming update of the `unblob`/`binwalk` forks (and the bug fixes on top) can be
validated against a known baseline rather than a guess. It is paired with the
executable harness in `tests/behavior/`.

fw2tar's reason to exist is **faithful, unprivileged preservation of filesystem
metadata** (modes, special bits, symlinks, ownership) so that rehosted firmware
behaves as it did on the device. The rehosting `unblob` fork carries 11 patches
almost entirely in service of this; see `fw2tar_issues/AUDIT-fork-drift.md`.

## The contract (what we intend to preserve)

For every extracted root filesystem, the output `*.rootfs.tar.gz` should reproduce,
for each entry:

| Property | Notes | Backing unblob patch(es) |
|---|---|---|
| Regular file mode (`rwx` bits) | e.g. `0644`, `0755`, `0600` | core / `a99f2415`, `c4241490` |
| Directory mode | incl. restrictive dirs like `0700` | core |
| **setuid / setgid / sticky** bits | `4755`, `2755`, `1777`, … | `f430f092` "Preserve suid/sgid/sticky bits" |
| Symlinks — **relative and absolute** | absolute links kept verbatim | `6f0ba150` "Allow absolute symlinks" |
| Executable bit (drives rootfs detection) | `find_linux_filesystems` counts these | — |
| Ownership (uid/gid) | preserved where the source carries it | UBI/yaffs patches `9fb1a7a7`, `f4b92c90` |

Device nodes are intentionally **stripped** (an unprivileged tar can't `mknod`); see
issue #53 for the open question of making that non-lossy.

## Filesystem-handler survey (rehosting unblob fork)

Every filesystem handler in the fork, the tool it shells out to, and whether we can
build a synthetic fixture for it unprivileged. The single most important pattern is
that **metadata fidelity tracks the extraction tool**, not the filesystem.

Fixture builders all live in the **test image** (`tests/behavior/Dockerfile`), which
extends the fw2tar image with the extra `mkfs`/`gen*` tools — so the harness needs only
docker, not host tooling.

| Handler | Extractor tool | Preserves metadata? | Fixture builder | Status |
|---|---|---|---|---|
| squashfs | `sasquatch` | **full** | `mksquashfs` | ✅ golden |
| ubi/ubifs | `ubireader_extract_files` | **full** (patch `9fb1a7a7`) | `mkfs.ubifs`+`ubinize` | ✅ golden |
| cramfs | `cramfsck -x` | **full** (LE) | `mkfs.cramfs` | ✅ golden (LE); BE gap (#5) |
| jffs2 | `jefferson` | **full** (unblob); binwalk loses dir modes | `mkfs.jffs2` | ✅ ok (unblob); ⚠️ binwalk known-bug |
| extfs (ext2/3/4) | `debugfs -R rdump` | **full** (unblob) | `mke2fs -d` | ✅ ok (unblob) |
| romfs | `RomfsExtractor` | keeps symlinks, **loses exec bits** | `genromfs -d` | ⛔ no-rootfs |
| iso9660 | Rock Ridge | base modes + exec bits; **loses suid/sgid/sticky + some symlinks** | `genisoimage -R` | ⚠️ diff |
| fat | `7z` | none (no unix metadata) | `mkfs.vfat`+`mcopy` | ⛔ no-rootfs |
| yaffs | `unyaffs` / `yaffshiv` | (patch `f4b92c90`, unverified) | `mkyaffs2image` (best-effort build) | gap (skipped) |
| ntfs | `7z` | n/a (ACLs, not unix) | — (`mkntfs` needs root/`ntfscp` to populate) | gap |

Archive/container handlers also produce directory trees and are common rootfs delivery
formats, so they're characterized too:

| Handler | Extractor tool | Preserves metadata? | Fixture builder | Status |
|---|---|---|---|---|
| tar | unblob `TarExtractor` | **full** | `tar` | ✅ ok (all extractors) |
| cpio | unblob `BinaryCPIOExtractor` | **full** (unblob + binwalk) | `cpio -H newc` | ✅ ok (unblob, binwalk) |
| zip | `7z` | **loses special bits** | `zip` | ⚠️ diff (all extractors) |
| ar, arc, arj, cab, rar, dmg, stuffit | `unar` / `7z` | varies; rare in Linux firmware | (not built) | not characterized |
| vendor wrappers (dlink, netgear, qnap, xiaomi, engeniustech, hp, instar) | custom | firmware-specific | — (need real firmware) | nightly only |

## Current characterized behavior (2026-06-22)

Measured by `tests/behavior/run.sh`, using one canonical synthetic rootfs
(`build_rootfs.py`) packed into each filesystem image and extracted with **every
extractor**. This is the gated matrix (`run_in_container.sh`):

```
fixture    unblob       binwalk      binwalkv3
squashfs   ok           ok           ok
cramfs     ok           ok           diff:6
ubifs      ok           ok           none
jffs2      ok           diff:3       diff:3
ext2       ok           none         none
ext3       ok           none         none
ext4       ok           none         none
romfs      none         none         diff:23
yaffs      skip         skip         skip
iso9660    diff:6       diff:6       none
fat        none         none         none
cpio       ok           ok           diff:6
tar        ok           ok           ok
zip        diff:6       diff:4       diff:6
squashfs_in_ext4 ok     none         ok
ext4_in_tar      ok     none         none
```

`ok` = full fidelity · `diff:N` = rootfs produced but N metadata mismatches ·
`none` = no rootfs produced · `skip` = fixture image not built (no builder).
Each `diff:N` count is **pinned** by the gate (see "Gate", below).

### Cross-extractor findings

- **squashfs** is the only type all three extractors handle perfectly.
- **cramfs and ubifs**: full fidelity under unblob *and* binwalk. binwalkv3 now
  extracts cramfs but loses metadata (`diff:6`), and still doesn't extract ubifs
  at all (`none`).
- **ext2/3/4**: **only unblob** produces a rootfs, now at full fidelity — `debugfs
  rdump` drops setuid/setgid/sticky, but unblob's extfs handler re-reads the inode
  modes from the image and re-applies them (rehosting/unblob#8). binwalk/binwalkv3
  produce nothing detectable.
- **romfs**: the mirror image — **only binwalkv3** produces a rootfs (with heavy perm
  loss, `diff:23`); unblob and binwalk produce nothing.
- **jffs2**: `jefferson` (the shared extractor) loses 3 directory modes, but unblob's
  jffs2 handler now re-applies them post-extraction (rehosting/unblob#5), so
  unblob is full-fidelity; binwalk/binwalkv3 call jefferson directly and still
  show `diff:3`.
- **tar** is preserved perfectly by all three extractors.
- **cpio**: unblob and binwalk are both perfect. unblob's own cpio extractor used
  to drop a bit on sticky directories (`opt/sticky 1777 → 1755`); fixed upstream in
  the fork (rehosting/unblob#4, re-applying dir modes deepest-first). binwalkv3
  doesn't handle cpio.
- **zip** (`7z` path) loses special bits everywhere (`busybox 4755→755`, sgid/sticky
  dropped) but unlike fat it *is* detected as a rootfs, because 7z does restore base
  modes + exec bits from zip's stored attributes — only the high bits are lost.

The unblob-only (ext) vs binwalkv3-only (romfs) split is concrete evidence for
why fw2tar runs **multiple extractors and picks the best** — no single extractor wins
across all types.

### Per-type metadata detail (best extractor)

| Filesystem | Best extractor | Base modes | suid/sgid/sticky | Dir modes | Symlinks |
|---|---|---|---|---|---|
| **squashfs** | any | ✓ | ✓ | ✓ | ✓ |
| **ubifs** | unblob/binwalk | ✓ | ✓ | ✓ | ✓ |
| **cramfs (LE)** | unblob/binwalk | ✓ | ✓ | ✓ | ✓ |
| **jffs2** | unblob (jefferson) | ✓ (files) | ✓ | ✓ (binwalk: reset to 0755) | ✓ |
| **ext2/3/4** | unblob (debugfs) | ✓ | ✓ | ✓ | ✓ |
| **romfs** | binwalkv3 | ✗ | ✗ | ✗ | (varies) |
| **iso9660** | unblob/binwalk (Rock Ridge) | ✓ | **✗ dropped** | ✓ | ✓ |
| **fat** | — (none) | ✗ | ✗ | ✗ | ✗ |
| **tar** | any | ✓ | ✓ | ✓ | ✓ |
| **cpio** | unblob / binwalk | ✓ | ✓ | ✓ | ✓ |
| **zip** | 7z | ✓ | **✗ dropped** | ✓ | ✓ |

### Known bugs encoded as fixtures

- **extfs (ext2/3/4) special bits — fixed for unblob.** `debugfs rdump` restores the
  base rwx modes and symlinks but drops the high bits (`bin/busybox 4755 → 755`,
  `opt/sgid 2750 → 750`, `opt/sticky 1777 → 777`). unblob's extfs handler now re-reads
  each inode's mode from the image (a batched `debugfs stat` pass) and re-applies it
  after `rdump`, so all three ext variants are full-fidelity (rehosting/unblob#8,
  issue #52). binwalk/binwalkv3 don't produce an ext rootfs at all.

- **jffs2 (jefferson) resets directory modes to 0755 — fixed for unblob.** `jefferson`
  flattens every non-755 directory (`opt/sgid 2750 → 755`, `opt/sticky 1777 → 755`,
  restrictive dirs like `var 0700 → 755`). Discovered by the harness (#54); unblob's
  jffs2 handler now re-applies the source directory modes after jefferson runs
  (rehosting/unblob#5), so **unblob is full-fidelity**. binwalk/binwalkv3 invoke
  jefferson directly and still exhibit the loss (`diff:3`).

- **7z-extracted filesystems (fat, ntfs) lose unix metadata wholesale.** `7z`
  does not restore unix perms; symlinks come out as empty files and exec
  bits are lost — so badly that `find_linux_filesystems` doesn't even recognize the tree
  as a rootfs (`<10` executables) and **no archive is produced**. Encoded as `no-rootfs`.
  This is the same class of problem that motivated moving cramfs off `7z` (fork commit
  `2342bb54`).

- **iso9660 now extracts as a rootfs (diff).** Unlike fat/ntfs, the iso9660 path
  restores Rock Ridge base modes + exec bits, so the tree *is* recognized as a rootfs
  (like zip); only the special bits (`busybox 4755→755`, sgid/sticky) and some symlinks
  (`usr/bin/sh` comes out as a file; absolute targets rewritten) are lost. Encoded as
  `diff` for unblob/binwalk; binwalkv3 still produces no rootfs.

- **romfs (`RomfsExtractor`) loses execute bits.** Symlinks survive with correct targets,
  but no file comes out executable, so — like the 7z types — the tree fails rootfs
  detection and no archive is produced. Encoded as `no-rootfs`.

### Nested filesystems (file type in another type)

Real firmware rarely ships a bare filesystem — the rootfs is usually carried
*inside* another container (a boot filesystem, a partitioned disk image). Two
fixtures exercise this directly by packing the canonical rootfs as an **inner**
filesystem inside an **outer** one:

- `squashfs_in_ext4` — an inner squashfs image stored inside an outer ext4.
- `ext4_in_tar` — an inner ext4 image stored inside an outer tar.

The contract here is *selection + fidelity*: fw2tar must recurse through the
outer layer, recognise the inner tree as the real Linux rootfs (the outer layer
holds a single image file, not a rootfs), and emit it at full fidelity. **unblob**
does this for both (`ok`). binwalkv3 handles `squashfs_in_ext4` but not
`ext4_in_tar`; binwalk recurses into neither (it doesn't produce an ext rootfs).

### No-cruft gate (clean rootfs, no scaffolding)

A correct fw2tar output is *only* the real rootfs. The underlying extractors
build a tree full of scaffolding — unblob nests `<name>_extract/` wrappers,
binwalk v3 nests `<name>.extracted/` wrappers, plus `<offset>-<offset>` /
`decompressed.bin` chunk artifacts, and (for a nested image) the inner container
file. fw2tar strips all of that during the tar walk (`archive.rs`
`is_extraction_artifact`) and rebases onto the selected rootfs. The
`check_behavior.py --strict-extras` mode fails on **any** archive entry that is
not part of the canonical rootfs (`lost+found` is the one allowed filesystem
artifact), and `run_in_container.sh` runs a dedicated **no-cruft gate** asserting
zero unexpected entries for the full-fidelity cells (`squashfs`, `cpio`, and both
nested fixtures).

### Recursion-cruft gate (artifact embedded *inside* the rootfs)

The sharper, real-world case: the genuine rootfs contains an extractable artifact
(a `.tar.gz`, a nested image, a compressed blob), and the extractor recursively
unpacks it **in place**, creating a sibling unpack directory right next to the
file *inside the rootfs tree*. That directory is not firmware and must not leak
into the output — but the artifact file itself must survive. The recursion-cruft
gate builds a rootfs carrying an embedded `usr/share/payload.tar.gz`
(`build_rootfs.py --embed-artifact`), extracts it with every extractor, and for
each one that produces a rootfs asserts the output is exactly *rootfs + the
artifact file*, with nothing unpacked from it (`--strict-extras`).

This caught a real bug: fw2tar's artifact-name list had `_extract` (unblob's
naming) but not `.extracted` (binwalk v3's), so **binwalk v3's in-tree recursion
directories — including the unpacked inner files — leaked into the archive**.
The root cause was structural: the output-strip (`archive.rs`) and the
rootfs-detection ignore-list (`directory_executables.rs`) were two separate
hand-maintained lists that had drifted. They are now a **single shared
`is_extraction_artifact`** (in `archive.rs`, used by both), covering every
convention observed across the extractors:

| convention | extractor | match |
|---|---|---|
| `<name>_extract` | unblob | suffix `_extract` |
| `<name>.uncompressed` / `.unknown` | unblob carve chunks | suffix |
| `<name>.extracted` (+ nested `0/`, `decompressed.bin`) | binwalk v3 | suffix `.extracted` |
| `squashfs-root`, `squashfs-root-0` | binwalk | prefix `squashfs-root` |
| `cpio-root` | binwalk | prefix `cpio-root` |
| `0.tar`, `0.tar.gz` | binwalk | prefix `0.tar` |

All three extractors are now clean on both `squashfs` and `ext4` carriers
(binwalk/binwalkv3 produce no ext rootfs, so those cells are reported, not gated).
Enumerated empirically by running each extractor directly and confirming every
recursion-dir name it emits matches a rule above.

### Caveats / gaps

- **Issue #52 ("everything → 0700").** A *clean* synthetic ext4 never reproduced the
  total `0700` collapse reported in #52 — current `debugfs rdump` keeps the base modes
  (including restrictive ones), losing only the special bits, which the extfs handler now
  restores (rehosting/unblob#8). The original catastrophic collapse was likely specific
  to an older `e2fsprogs`/`debugfs` or the real OpenWrt **combined disk image** (partition
  table → nested partition → ext4). That real image is now in the **nightly** suite
  (`end_to_end.sh`, with a mode-capturing baseline — `/tmp` 1777 etc.), covering the
  combined-disk path end-to-end; the `squashfs_in_ext4`/`ext4_in_tar` fixtures cover the
  synthetic fs-in-fs path on every run.

- **Big-endian cramfs (#5)** is uncovered: `mkfs.cramfs` emits host-endian (LE) only,
  which is why LE cramfs is green. A cross-endian fixture (or the real SRX5308 image in
  nightly) is needed for the BE regression.

- **yaffs** has no packaged builder; the test image attempts to compile
  `mkyaffs2image` from source (best-effort) and the harness **skips** the fixture if it
  isn't present. yaffs is notable — fork patch `f4b92c90` claims mode preservation but it
  is currently **unverified** by any test.
- **ntfs** has a builder (`mkntfs`) but populating an NTFS image from a directory needs
  root (mount) or per-file `ntfscp`, and NTFS carries ACLs rather than unix modes — low
  value, left as a gap.

## Testing model

The harness records an outcome for every `fixture × extractor` cell and gates it
against the `EXPECT` table in `run_in_container.sh`:

- `ok` — rootfs produced, every entry matches (full fidelity). A cell that drops from
  `ok` is a real regression.
- `diff:N` — rootfs produced but N metadata properties lost (a current bug). The count
  is **pinned exactly**: both worsening a bug (`diff:3 → diff:5`) and partially fixing
  one (`diff:3 → diff:1`) fail the gate, as does a full fix (`diff → ok`). Any of these
  is the signal to update `EXPECT` — either re-baselining the count or promoting the
  cell to `ok`.
- `none` — no rootfs produced (extractor can't handle the type, or loses so much that
  detection fails). A cell moving `none → diff/ok` means new coverage — update `EXPECT`.
- `skip` — fixture image couldn't be built (left ungated).

This characterizes current behavior honestly — encoding `diff`/`none` cells instead of
hiding them — while still catching any silent change in either direction.

## Running it

The harness runs entirely inside the Nix test image, so the host needs **docker**
and **nix**.

```sh
cd tests/behavior
./run.sh                  # nix build .#testImage, run all fixtures
./run.sh squashfs ext4    # a subset
KEEP_WORK=1 ./run.sh      # keep .work/ (images, logs, output) for inspection
# reuse an already-loaded image (e.g. built once in CI):
NO_BUILD=1 TEST_IMAGE=rehosting/fw2tar-test:latest ./run.sh
```

Pieces:
- `.#testImage` (flake output) — test image: fw2tar + the extra filesystem builders.
- `run.sh` — host entry: builds the test image with nix, then runs the driver inside it.
- `run_in_container.sh` — in-container driver: build rootfs → build images →
  `fakeroot_fw2tar` per fixture → check. Holds the fixture list + classifications.
- `build_rootfs.py` — builds the canonical rootfs + `expected.json` (the oracle).
- `make_images.sh` — packs it into each filesystem image, unprivileged.
- `check_behavior.py` — compares an output archive to `expected.json`; golden,
  `--expect-bug`, and `--types-only` semantics.

### In CI

- **Per PR** (`.github/workflows/build.yaml`) runs `cargo test` plus the harness on the
  **core** types against the freshly built image with no extra build —
  `NO_BUILD=1 TEST_IMAGE=<built image> ./tests/behavior/run.sh squashfs cramfs ubifs
  jffs2 ext2 ext3 ext4 cpio tar`. Deterministic, no firmware downloads.
- **Nightly** (`.github/workflows/nightly.yaml`) builds the behavior test image and runs
  the **full matrix** (`./tests/behavior/run.sh`), alongside the real-firmware
  `end_to_end.sh`.
